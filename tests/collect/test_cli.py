"""End-to-end: the CLI against the test database, with Djinni mocked by respx.

Unlike the other database tests, the CLI commits for real, so this module
empties the tables after each test.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
import structlog
from sqlalchemy import Engine, func, select, text

from tests.support import fixture_bytes, to_sqlalchemy
from vacancy_radar.collect.__main__ import main
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.db.models import RawVacancy
from vacancy_radar.sources.djinni import FEED_URL


@pytest.fixture(autouse=True)
def cli_database(
    migrated_engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", to_sqlalchemy(test_database_url))
    get_settings.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    get_sessionmaker().kw["bind"].dispose()
    get_sessionmaker.cache_clear()
    get_settings.cache_clear()
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE raw_vacancy, source RESTART IDENTITY"))
    # main() points structlog at pytest's captured stderr; undo that.
    structlog.reset_defaults()


def row_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(RawVacancy)) or 0


def log_events(stderr: str) -> list[dict[str, Any]]:
    # stderr is not a terminal under pytest, so every line is a JSON object.
    return [json.loads(line) for line in stderr.splitlines()]


def test_running_twice_stores_each_vacancy_once(
    respx_mock: respx.MockRouter, migrated_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.get(FEED_URL).respond(200, content=fixture_bytes("djinni/rss_all.xml"))

    assert main(["djinni"]) == 0
    assert main(["djinni"]) == 0

    assert row_count(migrated_engine) == 5
    first, second = log_events(capsys.readouterr().err)
    assert first["event"] == second["event"] == "collect.finished"
    assert (first["inserted"], first["unchanged"]) == (5, 0)
    assert (second["inserted"], second["unchanged"]) == (0, 5)


def test_dry_run_does_not_touch_the_database(
    respx_mock: respx.MockRouter, migrated_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.get(FEED_URL).respond(200, content=fixture_bytes("djinni/rss_all.xml"))

    assert main(["--dry-run"]) == 0

    assert row_count(migrated_engine) == 0
    [event] = log_events(capsys.readouterr().err)
    assert event["event"] == "collect.dry_run"
    assert event["fetched"] == 5


@pytest.mark.parametrize(
    "response",
    [httpx.Response(503), httpx.Response(200, content=b"<html>not a feed</html")],
)
def test_failed_source_exits_with_1(
    respx_mock: respx.MockRouter,
    migrated_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    response: httpx.Response,
) -> None:
    respx_mock.get(FEED_URL).mock(return_value=response)

    assert main([]) == 1

    assert row_count(migrated_engine) == 0
    [event] = log_events(capsys.readouterr().err)
    assert event["event"] == "collect.failed"
    assert event["level"] == "error"
    assert event["source"] == "djinni"


def test_unknown_source_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["workua"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
