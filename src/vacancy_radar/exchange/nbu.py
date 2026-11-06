"""Client for the NBU exchange-rate API (docs/sources.md → Reference data → NBU).

One request returns one currency's official rates for every day of a date
range, so filling any number of missing days takes one request per currency.
NBU answers only for days whose rate is already set, which includes the next
business day after 15:30 Kyiv time.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from vacancy_radar.domain.money import RateCurrency
from vacancy_radar.sources.base import FeedError

RANGE_URL = "https://bank.gov.ua/NBU_Exchange/exchange_site"


def fetch_rates(
    client: httpx.Client, currency: RateCurrency, start: date, end: date
) -> dict[date, Decimal]:
    """Hryvnias per unit of `currency` for each day from `start` to `end`.

    Days NBU has not set a rate for yet are missing from the result. Raises
    `httpx.HTTPError` on network and HTTP errors, `FeedError` on a response
    that cannot be parsed.
    """
    response = client.get(
        RANGE_URL,
        params={
            "start": f"{start:%Y%m%d}",
            "end": f"{end:%Y%m%d}",
            "valcode": currency.lower(),
            "sort": "exchangedate",
            "order": "asc",
            "json": "",
        },
    )
    response.raise_for_status()
    return parse_rates(response.content, currency)


def parse_rates(content: bytes, currency: RateCurrency) -> dict[date, Decimal]:
    """Turn an API response into `{day: hryvnias per unit}`. Pure: no I/O.

    Rates are read as `Decimal` straight from the JSON text, so no float
    rounding creeps in.
    """
    try:
        entries = json.loads(content, parse_float=Decimal)
    except ValueError as exc:
        raise FeedError(f"NBU response is not JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise FeedError(f"NBU response is not a list: {type(entries).__name__}")
    return dict(_parse_entry(entry, currency) for entry in entries)


def _parse_entry(entry: Any, currency: RateCurrency) -> tuple[date, Decimal]:
    try:
        if entry["cc"] != currency:
            raise FeedError(f"NBU returned {entry['cc']} instead of {currency}")
        # A calendar date, so there is no time zone to attach.
        day = datetime.strptime(entry["exchangedate"], "%d.%m.%Y").date()  # noqa: DTZ007
        rate = Decimal(entry["rate_per_unit"])
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise FeedError(f"NBU entry cannot be parsed: {entry!r}") from exc
    if rate <= 0:
        raise FeedError(f"NBU rate is not positive: {entry!r}")
    return day, rate
