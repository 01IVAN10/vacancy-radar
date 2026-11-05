"""DOU adapter: the public all-jobs RSS feed, under DOU's written consent.

The consent (docs/sources.md → DOU → Consent) allows only this one feed, at
most one request per 30 minutes including retries, with the agreed
User-Agent. Vacancy pages and category feeds are never fetched.
"""

import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from vacancy_radar.sources.base import FetchResult, RawItem
from vacancy_radar.sources.rss import Entry, parse_rss

FEED_URL = "https://jobs.dou.ua/vacancies/feeds/"

# `guid` is not used as the key: it ends in `?<timestamp>` that changes when a
# vacancy is republished. The numeric ID in the link stays the same.
VACANCY_ID = re.compile(r"^https://jobs\.dou\.ua/companies/[^/]+/vacancies/(\d+)/")


class DouAdapter:
    slug = "dou"
    name = "DOU"
    base_url = "https://jobs.dou.ua"
    # Consent condition 1. Never lower this without a new agreement with DOU.
    min_interval = timedelta(minutes=30)

    def fetch(self, client: httpx.Client) -> FetchResult:
        response = client.get(FEED_URL)
        response.raise_for_status()
        return parse_feed(response.content)


def parse_feed(content: bytes) -> FetchResult:
    """Turn the RSS document into raw items. Pure: no I/O."""
    return parse_rss(content, source="dou", to_raw_item=to_raw_item)


def to_raw_item(entry: Entry) -> RawItem | None:
    """Map one feed entry to a `RawItem`, or `None` if it has no vacancy ID.

    The payload keeps the raw RSS strings, including the link with its
    tracking parameter. Only `url` drops it.
    """
    link = str(entry.get("link", ""))
    match = VACANCY_ID.match(link)
    if match is None:
        return None

    payload = {
        "title": entry.get("title"),
        "link": link,
        "guid": entry.get("id"),
        "description": entry.get("summary"),
        "pubDate": entry.get("published"),
    }
    return RawItem(external_id=match[1], url=without_tracking(link), payload=payload)


def without_tracking(url: str) -> str:
    """Drop `utm_*` query parameters and keep everything else."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if not key.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))
