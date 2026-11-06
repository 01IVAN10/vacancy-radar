"""End-to-end: the CLI against the test database, with the sources mocked by respx.

Unlike the other database tests, the CLI commits for real, so this module
empties the tables after each test.
"""

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import structlog
from sqlalchemy import Engine, func, select

from tests.support import fixture_bytes
from vacancy_radar.collect.__main__ import main
from vacancy_radar.config import get_settings
from vacancy_radar.db.models import CollectRun, RawVacancy
from vacancy_radar.sources.djinni import FEED_URL, DjinniAdapter
from vacancy_radar.sources.dou import FEED_URL as DOU_FEED_URL


@pytest.fixture(autouse=True)
def cli_database(committed_database: Engine) -> Iterator[None]:
    yield
    # main() points structlog at pytest's captured stderr; undo that.
    structlog.reset_defaults()


def row_count(engine: Engine, model: type[RawVacancy | CollectRun] = RawVacancy) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(model)) or 0


def run_statuses(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.scalars(select(CollectRun.status).order_by(CollectRun.id)))


def log_events(stderr: str) -> list[dict[str, Any]]:
    # stderr is not a terminal under pytest, so every line is a JSON object.
    return [json.loads(line) for line in stderr.splitlines()]


DJINNI_FEED = fixture_bytes("djinni/rss_all.xml")
DOU_FEED = fixture_bytes("dou/rss_all.xml")


@pytest.fixture
def no_min_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let Djinni be fetched on every run, to test what happens on the second fetch."""
    monkeypatch.setattr(DjinniAdapter, "min_interval", timedelta(0))


@pytest.mark.usefixtures("no_min_interval")
def test_running_twice_stores_each_vacancy_once(
    respx_mock: respx.MockRouter, migrated_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.get(FEED_URL).respond(200, content=DJINNI_FEED)

    assert main(["djinni"]) == 0
    assert main(["djinni"]) == 0

    assert row_count(migrated_engine) == 5
    assert run_statuses(migrated_engine) == ["ok", "ok"]
    first, second = log_events(capsys.readouterr().err)
    assert first["event"] == second["event"] == "collect.finished"
    assert (first["inserted"], first["unchanged"]) == (5, 0)
    assert (second["inserted"], second["unchanged"]) == (0, 5)


def test_collects_every_source_by_default(
    respx_mock: respx.MockRouter, migrated_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.get(FEED_URL).respond(200, content=DJINNI_FEED)
    respx_mock.get(DOU_FEED_URL).respond(200, content=DOU_FEED)

    assert main([]) == 0

    assert row_count(migrated_engine) == 5 + 6
    events = log_events(capsys.readouterr().err)
    assert [(e["event"], e["source"]) for e in events] == [
        ("collect.finished", "djinni"),
        ("collect.finished", "dou"),
    ]


@pytest.mark.parametrize("source", ["djinni", "dou"])
def test_source_is_not_fetched_again_before_its_min_interval(
    respx_mock: respx.MockRouter,
    migrated_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    djinni = respx_mock.get(FEED_URL).respond(200, content=DJINNI_FEED)
    dou = respx_mock.get(DOU_FEED_URL).respond(200, content=DOU_FEED)

    assert main([source]) == 0
    assert main([source]) == 0

    route = {"djinni": djinni, "dou": dou}[source]
    assert route.call_count == 1
    assert run_statuses(migrated_engine) == ["ok"]
    skipped = log_events(capsys.readouterr().err)[-1]
    assert skipped["event"] == "collect.skipped"
    assert skipped["reason"] == "min_interval"
    assert skipped["source"] == source


def test_failed_request_counts_against_the_min_interval(
    respx_mock: respx.MockRouter, migrated_engine: Engine
) -> None:
    # DOU's consent limits requests, not successful ones: no retry inside 30 min.
    route = respx_mock.get(DOU_FEED_URL).respond(503)

    assert main(["dou"]) == 1
    assert main(["dou"]) == 0

    assert route.call_count == 1
    assert run_statuses(migrated_engine) == ["failed"]


def test_dry_run_stores_no_items_but_records_the_request(
    respx_mock: respx.MockRouter, migrated_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    route = respx_mock.get(DOU_FEED_URL).respond(200, content=DOU_FEED)

    assert main(["dou", "--dry-run"]) == 0
    # The dry run was a request too, so a real run right after must wait.
    assert main(["dou"]) == 0

    assert route.call_count == 1
    assert row_count(migrated_engine) == 0
    assert run_statuses(migrated_engine) == ["dry_run"]
    dry_run, skipped = log_events(capsys.readouterr().err)
    assert (dry_run["event"], dry_run["fetched"]) == ("collect.dry_run", 6)
    assert (skipped["event"], skipped["reason"]) == ("collect.skipped", "min_interval")


def test_disabled_source_is_never_fetched(
    respx_mock: respx.MockRouter,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DISABLED_SOURCES", "dou")
    get_settings.cache_clear()
    respx_mock.get(FEED_URL).respond(200, content=DJINNI_FEED)
    dou = respx_mock.get(DOU_FEED_URL).respond(200, content=DOU_FEED)

    # Naming the source explicitly does not override the kill switch.
    assert main(["djinni", "dou"]) == 0

    assert dou.call_count == 0
    assert row_count(migrated_engine) == 5
    events = log_events(capsys.readouterr().err)
    assert (events[-1]["event"], events[-1]["reason"]) == ("collect.skipped", "disabled")


@pytest.mark.parametrize(
    "response",
    [httpx.Response(503), httpx.Response(200, content=b"<html>not a feed</html")],
)
def test_failed_source_exits_with_1_and_the_others_still_run(
    respx_mock: respx.MockRouter,
    migrated_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    response: httpx.Response,
) -> None:
    respx_mock.get(FEED_URL).mock(return_value=response)
    respx_mock.get(DOU_FEED_URL).respond(200, content=DOU_FEED)

    assert main([]) == 1

    assert row_count(migrated_engine) == 6
    assert run_statuses(migrated_engine) == ["failed", "ok"]
    failed, finished = log_events(capsys.readouterr().err)
    assert (failed["event"], failed["level"], failed["source"]) == (
        "collect.failed",
        "error",
        "djinni",
    )
    assert (finished["event"], finished["source"]) == ("collect.finished", "dou")


def test_error_outside_http_is_recorded_and_the_others_still_run(
    respx_mock: respx.MockRouter,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken_parser(content: bytes) -> None:
        raise ValueError("parser bug")

    monkeypatch.setattr("vacancy_radar.sources.djinni.parse_feed", broken_parser)
    respx_mock.get(FEED_URL).respond(200, content=DJINNI_FEED)
    respx_mock.get(DOU_FEED_URL).respond(200, content=DOU_FEED)

    assert main([]) == 1

    assert run_statuses(migrated_engine) == ["failed", "ok"]
    failed, _ = log_events(capsys.readouterr().err)
    assert (failed["event"], failed["error_type"]) == ("collect.failed", "ValueError")


def test_runs_without_redis_url(
    respx_mock: respx.MockRouter,
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The scheduled GitHub Actions collection has no Redis.
    monkeypatch.delenv("REDIS_URL")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    respx_mock.get(FEED_URL).respond(200, content=DJINNI_FEED)

    assert main(["djinni"]) == 0

    assert get_settings().redis_url is None
    assert row_count(migrated_engine) == 5


def test_unknown_source_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["workua"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
