"""The rate cache, against real Postgres with NBU mocked by respx."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.db.models import ExchangeRate
from vacancy_radar.exchange.nbu import RANGE_URL
from vacancy_radar.exchange.rates import (
    REQUEST_GAP_SECONDS,
    RateTable,
    ensure_rates,
    store_rates,
)
from vacancy_radar.sources.http import build_client

USD = fixture_bytes("nbu/usd_20261030_20261107.json")
EUR = fixture_bytes("nbu/eur_20261030_20261107.json")
NOV_5 = date(2026, 11, 5)
NOV_7 = date(2026, 11, 7)


@pytest.fixture
def nbu() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(RANGE_URL, params={"valcode": "usd"}).respond(200, content=USD)
        router.get(RANGE_URL, params={"valcode": "eur"}).respond(200, content=EUR)
        yield router


def ensure(session: Session, *days: date, pauses: list[float] | None = None) -> RateTable:
    """`ensure_rates` with a real client and the pauses recorded instead of slept."""
    recorded = pauses if pauses is not None else []
    with build_client() as client:
        return ensure_rates(session, client, days, sleep=recorded.append)


def stored_rows(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ExchangeRate)) or 0


def test_fetches_missing_days_as_one_range_per_currency(
    db_session: Session, nbu: respx.MockRouter
) -> None:
    pauses: list[float] = []

    table = ensure(db_session, NOV_7, NOV_5, pauses=pauses)

    assert table == {
        NOV_5: {"USD": Decimal("45.5375"), "EUR": Decimal("51.2489")},
        NOV_7: {"USD": Decimal("45.4432"), "EUR": table[NOV_7]["EUR"]},
    }
    requests = [call.request.url.params for call in nbu.calls]
    assert [(p["valcode"], p["start"], p["end"]) for p in requests] == [
        ("usd", "20261105", "20261107"),
        ("eur", "20261105", "20261107"),
    ]
    assert pauses == [REQUEST_GAP_SECONDS]
    # Everything NBU returned is kept, not only the days asked for.
    assert stored_rows(db_session) == 2 * 9


def test_stored_days_are_not_requested_again(db_session: Session, nbu: respx.MockRouter) -> None:
    first = ensure(db_session, NOV_5)
    calls = nbu.calls.call_count

    second = ensure(db_session, NOV_5, date(2026, 11, 1))

    assert second[NOV_5] == first[NOV_5]
    assert nbu.calls.call_count == calls


def test_a_day_with_one_currency_stored_is_fetched_again(
    db_session: Session, nbu: respx.MockRouter
) -> None:
    store_rates(db_session, "USD", {NOV_5: Decimal("45.5375")})

    table = ensure(db_session, NOV_5)

    assert set(table[NOV_5]) == {"USD", "EUR"}
    assert nbu.calls.call_count == 2


def test_a_day_without_a_rate_yet_takes_the_latest_earlier_one(
    db_session: Session, nbu: respx.MockRouter
) -> None:
    table = ensure(db_session, date(2026, 11, 10))

    assert table[date(2026, 11, 10)]["USD"] == Decimal("45.4432")  # the rate of Nov 7


def test_a_day_with_no_recent_rate_is_left_out(db_session: Session, nbu: respx.MockRouter) -> None:
    assert ensure(db_session, date(2026, 12, 31)) == {}


def test_no_days_means_no_requests(db_session: Session, nbu: respx.MockRouter) -> None:
    assert ensure(db_session) == {}
    assert nbu.calls.call_count == 0


def test_storing_the_same_rates_twice_keeps_one_row(db_session: Session) -> None:
    store_rates(db_session, "USD", {NOV_5: Decimal("45.5375")})
    store_rates(db_session, "USD", {NOV_5: Decimal("45.5375")})
    store_rates(db_session, "USD", {})

    assert stored_rows(db_session) == 1


@respx.mock
def test_nbu_errors_propagate_and_store_nothing(db_session: Session) -> None:
    respx.get(RANGE_URL).respond(503)

    with pytest.raises(httpx.HTTPStatusError):
        ensure(db_session, NOV_5)

    assert stored_rows(db_session) == 0


def test_fallback_skips_an_earlier_day_with_one_currency(
    db_session: Session, nbu: respx.MockRouter
) -> None:
    store_rates(db_session, "USD", {date(2026, 11, 9): Decimal("45.3206")})

    table = ensure(db_session, date(2026, 11, 10))

    assert table[date(2026, 11, 10)]["USD"] == Decimal("45.4432")  # Nov 7, not Nov 9
