import json

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from tests.support import fixture_bytes
from vacancy_radar.sources import ADAPTERS
from vacancy_radar.sources.base import FeedError
from vacancy_radar.sources.djinni import FEED_URL, DjinniAdapter, parse_feed
from vacancy_radar.sources.http import USER_AGENT, build_client

ALL_JOBS = fixture_bytes("djinni/rss_all.xml")
PYTHON_JOBS = fixture_bytes("djinni/rss_python.xml")


def rss(*items: str) -> bytes:
    body = "".join(f"<item>{item}</item>" for item in items)
    return f"<rss version='2.0'><channel>{body}</channel></rss>".encode()


# --- parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("feed", "expected_ids"),
    [
        (ALL_JOBS, ["852239", "856858", "856870", "856869", "850004"]),
        (PYTHON_JOBS, ["856775", "856641", "856036", "832898", "852433"]),
    ],
)
def test_takes_external_id_from_the_numeric_part_of_the_link(
    feed: bytes, expected_ids: list[str]
) -> None:
    result = parse_feed(feed)

    assert [item.external_id for item in result.items] == expected_ids
    assert result.skipped == 0


def test_keeps_raw_fields_unchanged() -> None:
    item = parse_feed(ALL_JOBS).items[0]

    assert item.url == "https://djinni.co/jobs/852239-b2b-sales-manager-account-executive/"
    assert item.payload["link"] == item.url
    assert item.payload["guid"] == item.url
    assert item.payload["title"] == "B2B Sales Manager / Account Executive"
    # The date stays the RFC 822 string; parsing it is normalization's job.
    assert item.payload["pubDate"] == "Thu, 05 Nov 2026 16:10:00 +0200"
    assert item.payload["categories"] == ["Sales Manager"]


def test_keeps_description_html_unsanitized() -> None:
    description = parse_feed(ALL_JOBS).items[0].payload["description"]

    assert "<strong>40 000 - 60 000 грн" in description
    assert "&nbsp;" in description


def test_payload_contains_only_json_values() -> None:
    for item in parse_feed(ALL_JOBS).items + parse_feed(PYTHON_JOBS).items:
        assert json.loads(json.dumps(item.payload)) == item.payload
        assert set(item.payload) == {
            "title",
            "link",
            "guid",
            "description",
            "pubDate",
            "categories",
        }


def test_skips_items_without_a_job_id() -> None:
    feed = rss(
        "<title>No id</title><link>https://djinni.co/jobs/</link>",
        "<title>Other site</title><link>https://example.com/jobs/1-x/</link>",
        "<title>Good</title><link>https://djinni.co/jobs/123-good/</link>",
    )

    result = parse_feed(feed)

    assert [item.external_id for item in result.items] == ["123"]
    assert result.skipped == 2


def test_empty_feed_is_not_an_error() -> None:
    assert parse_feed(rss()).items == []


@pytest.mark.parametrize(
    "content", [b"<html>Service unavailable</html", b"<rss><channel><item><title>x</broken>"]
)
def test_unparseable_feed_raises(content: bytes) -> None:
    with pytest.raises(FeedError, match="cannot be parsed"):
        parse_feed(content)


def test_partly_broken_feed_keeps_the_good_items_and_warns() -> None:
    feed = rss("<title>Good</title><link>https://djinni.co/jobs/123-good/</link>")[:-6]

    with capture_logs() as logs:
        result = parse_feed(feed)

    assert [item.external_id for item in result.items] == ["123"]
    assert [log["event"] for log in logs] == ["djinni.feed_malformed"]


# --- fetching ----------------------------------------------------------------


def test_adapter_is_registered_by_slug() -> None:
    assert isinstance(ADAPTERS["djinni"], DjinniAdapter)


def test_fetches_the_all_jobs_feed_with_the_project_user_agent(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(FEED_URL).respond(200, content=ALL_JOBS)

    with build_client() as client:
        result = DjinniAdapter().fetch(client)

    assert route.call_count == 1
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT
    assert len(result.items) == 5


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_http_error_status_raises(respx_mock: respx.MockRouter, status: int) -> None:
    respx_mock.get(FEED_URL).respond(status)

    with build_client() as client, pytest.raises(httpx.HTTPStatusError):
        DjinniAdapter().fetch(client)


def test_timeout_raises(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(FEED_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

    with build_client() as client, pytest.raises(httpx.TimeoutException):
        DjinniAdapter().fetch(client)
