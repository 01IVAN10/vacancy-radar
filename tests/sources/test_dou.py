import json

import httpx
import pytest
import respx

from tests.support import fixture_bytes
from vacancy_radar.sources import ADAPTERS
from vacancy_radar.sources.base import FeedError
from vacancy_radar.sources.dou import FEED_URL, DouAdapter, parse_feed, without_tracking
from vacancy_radar.sources.http import USER_AGENT, build_client

ALL_JOBS = fixture_bytes("dou/rss_all.xml")


def rss(*items: str) -> bytes:
    body = "".join(f"<item>{item}</item>" for item in items)
    return f"<rss version='2.0'><channel>{body}</channel></rss>".encode()


def link(vacancy_id: str, company: str = "acme") -> str:
    return f"https://jobs.dou.ua/companies/{company}/vacancies/{vacancy_id}/?utm_source=jobsrss"


# --- parsing -----------------------------------------------------------------


def test_takes_external_id_from_the_vacancy_number_in_the_link() -> None:
    result = parse_feed(ALL_JOBS)

    assert [item.external_id for item in result.items] == [
        "379095",
        "379092",
        "379090",
        "374387",
        "379075",
        "379053",
    ]
    assert result.skipped == 0


def test_url_drops_the_tracking_parameter_but_payload_keeps_the_raw_link() -> None:
    item = parse_feed(ALL_JOBS).items[3]

    assert item.url == "https://jobs.dou.ua/companies/innovation-group/vacancies/374387/"
    assert item.payload["link"] == (
        "https://jobs.dou.ua/companies/innovation-group/vacancies/374387/?utm_source=jobsrss"
    )
    assert item.payload["guid"] == (
        "https://jobs.dou.ua/companies/innovation-group/vacancies/374387/?1793898204"
    )
    assert item.payload["title"] == "Senior Data Engineer в Innovation Group, Одеса, віддалено"
    # The date stays the RFC 822 string; parsing it is normalization's job.
    assert item.payload["pubDate"] == "Thu, 05 Nov 2026 19:03:24 +0200"


def test_keeps_title_text_as_published() -> None:
    titles = [item.payload["title"] for item in parse_feed(ALL_JOBS).items]

    # Salary, cities, "abroad" and "remote" stay in the title for normalization.
    assert "Інженер-пілот БпЛА (R&amp;D) в Orients, $900\u20132200, Львів" in titles
    # Look-alike letters are kept as sent: "CoinPayments" starts with a Cyrillic Es (U+0421).
    assert titles[0].startswith("Outbound Growth Manager в \u0421oinPayments")


def test_payload_contains_only_json_values() -> None:
    for item in parse_feed(ALL_JOBS).items:
        assert json.loads(json.dumps(item.payload)) == item.payload
        assert set(item.payload) == {"title", "link", "guid", "description", "pubDate"}
        assert item.payload["description"]


def test_republished_vacancy_keeps_its_external_id() -> None:
    # DOU moves a republished vacancy to the top with a new guid timestamp.
    first = rss(f"<title>A</title><link>{link('1')}</link><guid>{link('1')}?1</guid>")
    again = rss(f"<title>A</title><link>{link('1')}</link><guid>{link('1')}?2</guid>")

    [before] = parse_feed(first).items
    [after] = parse_feed(again).items

    assert before.external_id == after.external_id == "1"
    assert before.payload["guid"] != after.payload["guid"]


def test_skips_items_without_a_vacancy_id() -> None:
    feed = rss(
        "<title>Company page</title><link>https://jobs.dou.ua/companies/acme/</link>",
        "<title>Other site</title><link>https://example.com/vacancies/1/</link>",
        f"<title>Good</title><link>{link('123')}</link>",
    )

    result = parse_feed(feed)

    assert [item.external_id for item in result.items] == ["123"]
    assert result.skipped == 2


def test_unparseable_feed_raises() -> None:
    with pytest.raises(FeedError, match="dou feed cannot be parsed"):
        parse_feed(b"<html>Just a moment...</html")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (link("1"), "https://jobs.dou.ua/companies/acme/vacancies/1/"),
        (
            "https://jobs.dou.ua/companies/acme/vacancies/1/?utm_source=x&utm_medium=y&ref=z",
            "https://jobs.dou.ua/companies/acme/vacancies/1/?ref=z",
        ),
        (
            "https://jobs.dou.ua/companies/acme/vacancies/1/",
            "https://jobs.dou.ua/companies/acme/vacancies/1/",
        ),
    ],
)
def test_without_tracking(url: str, expected: str) -> None:
    assert without_tracking(url) == expected


# --- fetching ----------------------------------------------------------------


def test_adapter_is_registered_by_slug() -> None:
    assert isinstance(ADAPTERS["dou"], DouAdapter)


def test_min_interval_matches_the_consent() -> None:
    # docs/sources.md → DOU → Consent, condition 1.
    assert DouAdapter.min_interval.total_seconds() >= 30 * 60


def test_fetches_only_the_all_jobs_feed_with_the_agreed_user_agent(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(FEED_URL).respond(200, content=ALL_JOBS)

    with build_client() as client:
        result = DouAdapter().fetch(client)

    assert FEED_URL == "https://jobs.dou.ua/vacancies/feeds/"
    assert route.call_count == 1
    # The User-Agent, version included, is part of the consent.
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT
    assert USER_AGENT == "vacancy-radar/0.1 (+https://github.com/01IVAN10/vacancy-radar)"
    assert len(result.items) == 6


@pytest.mark.parametrize("status", [403, 429, 503])
def test_http_error_status_raises(respx_mock: respx.MockRouter, status: int) -> None:
    respx_mock.get(FEED_URL).respond(status)

    with build_client() as client, pytest.raises(httpx.HTTPStatusError):
        DouAdapter().fetch(client)
