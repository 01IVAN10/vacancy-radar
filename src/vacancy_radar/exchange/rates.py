"""Rates for the days vacancies were published on, cached in `exchange_rate`.

`ensure_rates` is what normalization calls. It reads the cache, asks NBU only
for days that are not stored yet, and stores what NBU returns. Rates never
change once set, so a day is requested at most once in practice, and storing
uses `ON CONFLICT DO NOTHING`, which makes two runs at once safe.
"""

import time
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vacancy_radar.db.models import ExchangeRate
from vacancy_radar.domain.money import RATE_CURRENCIES, RateCurrency
from vacancy_radar.exchange.nbu import fetch_rates

RateTable = dict[date, dict[RateCurrency, Decimal]]
# Typed lookup for the currency codes read back from the table.
CURRENCY_CODES: dict[str, RateCurrency] = {currency: currency for currency in RATE_CURRENCIES}

# The collection rules allow at most 1 request per second per domain. The
# worker's client enforces it through Redis; the CLI has no Redis, so the
# requests to NBU are spaced out here as well.
REQUEST_GAP_SECONDS = 1.0
# A day NBU has no rate for yet takes the latest earlier rate, if it is this recent.
MAX_FALLBACK = timedelta(days=7)


def ensure_rates(
    session: Session,
    client: httpx.Client,
    days: Iterable[date],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> RateTable:
    """USD and EUR rates for each of `days`, fetching only the missing ones.

    Missing days are fetched as one range per currency. A day NBU has no rate
    for yet gets the latest stored rate up to `MAX_FALLBACK` earlier; a day
    with neither is left out of the result. Errors from NBU propagate, and
    nothing is committed: the caller's transaction decides.
    """
    wanted = set(days)
    table = load_rates(session, wanted)
    missing = sorted(day for day in wanted if not _is_complete(table.get(day)))
    if missing:
        for i, currency in enumerate(RATE_CURRENCIES):
            if i:
                sleep(REQUEST_GAP_SECONDS)
            store_rates(session, currency, fetch_rates(client, currency, missing[0], missing[-1]))
        table = load_rates(session, wanted)

    for day in sorted(wanted):
        if not _is_complete(table.get(day)) and (earlier := latest_rates_before(session, day)):
            table[day] = earlier
    return {day: rates for day, rates in table.items() if _is_complete(rates)}


def load_rates(session: Session, days: Iterable[date]) -> RateTable:
    """Stored rates for `days`, possibly incomplete."""
    table: RateTable = {}
    rows = session.execute(
        select(ExchangeRate.day, ExchangeRate.currency, ExchangeRate.uah_per_unit).where(
            ExchangeRate.day.in_(list(days))
        )
    )
    for day, currency, rate in rows:
        table.setdefault(day, {})[CURRENCY_CODES[currency]] = rate
    return table


def store_rates(session: Session, currency: RateCurrency, rates: dict[date, Decimal]) -> None:
    """Insert rates; days already stored are left as they are."""
    if not rates:
        return
    session.execute(
        insert(ExchangeRate)
        .values(
            [
                {"day": day, "currency": currency, "uah_per_unit": rate}
                for day, rate in rates.items()
            ]
        )
        .on_conflict_do_nothing(index_elements=[ExchangeRate.day, ExchangeRate.currency])
    )


def latest_rates_before(session: Session, day: date) -> dict[RateCurrency, Decimal] | None:
    """The most recent complete set of rates before `day`, within `MAX_FALLBACK`."""
    earlier = load_rates(
        session,
        [day - timedelta(days=n) for n in range(1, MAX_FALLBACK.days + 1)],
    )
    for candidate in sorted(earlier, reverse=True):
        if _is_complete(earlier[candidate]):
            return earlier[candidate]
    return None


def _is_complete(rates: dict[RateCurrency, Decimal] | None) -> bool:
    return rates is not None and all(currency in rates for currency in RATE_CURRENCIES)
