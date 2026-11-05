"""Djinni adapter: the public all-jobs RSS feed.

Only the feed is read, never individual job pages (see docs/sources.md). The
all-jobs feed mixes IT and non-IT roles; everything is stored and filtered at
normalization time.
"""

import re
from datetime import timedelta

import httpx

from vacancy_radar.sources.base import FetchResult, RawItem
from vacancy_radar.sources.rss import Entry, parse_rss

FEED_URL = "https://djinni.co/jobs/rss/"

# The numeric ID is stable; the slug after it changes when the title is edited.
JOB_ID = re.compile(r"^https://djinni\.co/jobs/(\d+)-")


class DjinniAdapter:
    slug = "djinni"
    name = "Djinni"
    base_url = "https://djinni.co"
    # About one request per 30 min on a 15-min schedule, with room for cron jitter.
    min_interval = timedelta(minutes=25)

    def fetch(self, client: httpx.Client) -> FetchResult:
        response = client.get(FEED_URL)
        response.raise_for_status()
        return parse_feed(response.content)


def parse_feed(content: bytes) -> FetchResult:
    """Turn the RSS document into raw items. Pure: no I/O."""
    return parse_rss(content, source="djinni", to_raw_item=to_raw_item)


def to_raw_item(entry: Entry) -> RawItem | None:
    """Map one feed entry to a `RawItem`, or `None` if it has no job ID.

    Payload keys are the RSS element names and values are the raw strings,
    not feedparser's derived fields (`published_parsed` and so on).
    """
    link = str(entry.get("link", ""))
    match = JOB_ID.match(link)
    if match is None:
        return None

    payload = {
        "title": entry.get("title"),
        "link": link,
        "guid": entry.get("id"),
        "description": entry.get("summary"),
        "pubDate": entry.get("published"),
        "categories": [tag.get("term") for tag in entry.get("tags", [])],
    }
    return RawItem(external_id=match[1], url=link, payload=payload)
