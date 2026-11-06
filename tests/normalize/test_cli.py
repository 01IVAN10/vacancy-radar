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


def events(stderr: str) -> dict[str, dict[str, Any]]:
    """The logged events by name."""
    parsed = [json.loads(line) for line in stderr.splitlines()]
    return {event["event"]: event for event in parsed}


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
    first = events(capsys.readouterr().err)
    assert main([]) == 0
    second = events(capsys.readouterr().err)

    assert (
        first["normalize.finished"]["normalized"],
        first["normalize.finished"]["with_salary"],
    ) == (
        5,
        2,
    )
    assert first["normalize.converted"]["converted"] == 1
    assert second["normalize.finished"]["normalized"] == 0
    assert second["normalize.converted"]["converted"] == 0
    assert vacancy_count(committed_database) == 5


def test_rebuild_normalizes_everything_again(
    respx_mock: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    mock_nbu(respx_mock)
    main([])

    assert main(["--rebuild"]) == 0
    event = events(capsys.readouterr().err)["normalize.finished"]
    assert (event["rebuild"], event["normalized"]) == (True, 5)


def test_nbu_failure_exits_with_1_after_normalizing(
    respx_mock: respx.MockRouter, committed_database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.get(RANGE_URL).respond(503)

    assert main([]) == 1
    logged = events(capsys.readouterr().err)
    assert logged["normalize.finished"]["normalized"] == 5
    assert logged["normalize.conversion_failed"]["error_type"] == "HTTPStatusError"
    assert vacancy_count(committed_database) == 5
