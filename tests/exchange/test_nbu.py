from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from tests.support import fixture_bytes
from vacancy_radar.domain.money import RateCurrency
from vacancy_radar.exchange.nbu import RANGE_URL, fetch_rates, parse_rates
from vacancy_radar.sources.base import FeedError
from vacancy_radar.sources.http import USER_AGENT, build_client

USD = fixture_bytes("nbu/usd_20261030_20261107.json")
EUR = fixture_bytes("nbu/eur_20261030_20261107.json")


def test_parses_a_rate_for_every_calendar_day() -> None:
    rates = parse_rates(USD, "USD")

    assert list(rates) == [date(2026, 10, 30 + n) for n in range(2)] + [
        date(2026, 11, n) for n in range(1, 8)
    ]
    # Weekend days repeat the last business day's rate.
    assert rates[date(2026, 10, 31)] == rates[date(2026, 11, 1)] == Decimal("45.5293")
    assert rates[date(2026, 11, 5)] == Decimal("45.5375")


def test_reads_rates_as_exact_decimals() -> None:
    rates = parse_rates(EUR, "EUR")

    assert rates[date(2026, 11, 5)] == Decimal("51.2489")
    assert all(isinstance(rate, Decimal) for rate in rates.values())


def test_empty_list_means_no_rate_set_yet() -> None:
    assert parse_rates(b"[\r\n]\r\n", "USD") == {}


@pytest.mark.parametrize(
    ("content", "currency"),
    [
        pytest.param(b"<html>Service unavailable</html>", "USD", id="not-json"),
        pytest.param(b'{"error": "bad request"}', "USD", id="not-a-list"),
        pytest.param(EUR, "USD", id="other-currency"),
        pytest.param(b'[{"cc": "USD", "exchangedate": "2026-11-05"}]', "USD", id="no-rate"),
        pytest.param(
            b'[{"cc": "USD", "exchangedate": "05/11/2026", "rate_per_unit": 45.5}]',
            "USD",
            id="date-format",
        ),
        pytest.param(
            b'[{"cc": "USD", "exchangedate": "05.11.2026", "rate_per_unit": 0}]',
            "USD",
            id="zero-rate",
        ),
        pytest.param(b"[42]", "USD", id="entry-not-an-object"),
    ],
)
def test_rejects_responses_that_cannot_be_parsed(content: bytes, currency: RateCurrency) -> None:
    with pytest.raises(FeedError):
        parse_rates(content, currency)


@respx.mock
def test_requests_one_range_for_one_currency() -> None:
    route = respx.get(
        RANGE_URL,
        params={
            "start": "20261030",
            "end": "20261107",
            "valcode": "eur",
            "sort": "exchangedate",
            "order": "asc",
            "json": "",
        },
    ).respond(200, content=EUR)

    with build_client() as client:
        rates = fetch_rates(client, "EUR", date(2026, 10, 30), date(2026, 11, 7))

    assert len(rates) == 9
    assert route.call_count == 1
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT


@respx.mock
def test_raises_on_http_errors() -> None:
    respx.get(RANGE_URL).respond(503)

    with build_client() as client, pytest.raises(httpx.HTTPStatusError):
        fetch_rates(client, "USD", date(2026, 11, 5), date(2026, 11, 5))
