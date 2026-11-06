"""The CLI against the test database: it commits for real, so tables are emptied after."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
import respx
import structlog
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.collect.service import ensure_source, upsert_raw_items
from vacancy_radar.db.models import Vacancy
from vacancy_radar.exchange import rates
from vacancy_radar.exchange.nbu import RANGE_URL
from vacancy_radar.normalize.__main__ import main
from vacancy_radar.sources import ADAPTERS, djinni


@pytest.fixture(autouse=True)
def stored_items(committed_database: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with Session(committed_database) as session:
        source_id = ensure_source(session, ADAPTERS["djinni"])
        upsert_raw_items(
            session, source_id, djinni.parse_feed(fixture_bytes("djinni/rss_all.xml")).items
        )
        session.commit()
    # The pause between NBU requests is tested in tests/exchange.
    monkeypatch.setattr(rates, "REQUEST_GAP_SECONDS", 0.0)
    yield
    structlog.reset_defaults()


def vacancy_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(Vacancy)) or 0


def last_event(stderr: str) -> dict[str, Any]:
    event: dict[str, Any] = json.loads(stderr.splitlines()[-1])
    return event


def mock_nbu(router: respx.MockRouter) -> None:
    for currency in ("usd", "eur"):
        router.get(RANGE_URL, params={"valcode": currency}).respond(
            200, content=fixture_bytes(f"nbu/{currency}_20261030_20261107.json")
        )


def test_normalizes_pending_items_and_logs_the_counts(
    respx_mock: respx.MockRouter, committed_database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_nbu(respx_mock)

    assert main([]) == 0
    first = last_event(capsys.readouterr().err)
    assert main([]) == 0
    second = last_event(capsys.readouterr().err)

    assert (first["event"], first["normalized"], first["with_salary"]) == (
        "normalize.finished",
        5,
        2,
    )
    assert second["normalized"] == 0
    assert vacancy_count(committed_database) == 5


def test_rebuild_normalizes_everything_again(
    respx_mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_nbu(respx_mock)
    main([])

    assert main(["--rebuild"]) == 0
    event = last_event(capsys.readouterr().err)
    assert (event["rebuild"], event["normalized"]) == (True, 5)


def test_nbu_failure_exits_with_1(
    respx_mock: respx.MockRouter, committed_database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.get(RANGE_URL).respond(503)

    assert main([]) == 1
    event = last_event(capsys.readouterr().err)
    assert (event["event"], event["error_type"]) == ("normalize.failed", "HTTPStatusError")
    assert vacancy_count(committed_database) == 0
