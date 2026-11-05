"""Djinni adapter: the public all-jobs RSS feed.

Only the feed is read, never individual job pages (see docs/sources.md). The
all-jobs feed mixes IT and non-IT roles; everything is stored and filtered at
normalization time.
"""

import re
from collections.abc import Mapping
from typing import Any

import feedparser
import httpx
import structlog

from vacancy_radar.sources.base import FeedError, FetchResult, RawItem

FEED_URL = "https://djinni.co/jobs/rss/"

# The numeric ID is stable; the slug after it changes when the title is edited.
JOB_ID = re.compile(r"^https://djinni\.co/jobs/(\d+)-")

logger = structlog.get_logger(__name__)


class DjinniAdapter:
    slug = "djinni"
    name = "Djinni"
    base_url = "https://djinni.co"

    def fetch(self, client: httpx.Client) -> FetchResult:
        response = client.get(FEED_URL)
        response.raise_for_status()
        return parse_feed(response.content)


def parse_feed(content: bytes) -> FetchResult:
    """Turn the RSS document into raw items. Pure: no I/O.

    HTML sanitizing and URI rewriting are off, so the description is stored
    exactly as Djinni sent it. It is sanitized when shown, not when stored.
    """
    feed = feedparser.parse(content, sanitize_html=False, resolve_relative_uris=False)

    items: list[RawItem] = []
    skipped = 0
    for entry in feed.entries:
        item = to_raw_item(entry)
        if item is None:
            skipped += 1
            logger.warning("djinni.item_skipped", link=entry.get("link"))
        else:
            items.append(item)

    # feedparser recovers from many XML errors; fail only if nothing usable is left.
    if feed.bozo:
        if not items:
            raise FeedError(f"Djinni feed cannot be parsed: {feed.bozo_exception}")
        logger.warning("djinni.feed_malformed", error=str(feed.bozo_exception))

    return FetchResult(items=items, skipped=skipped)


def to_raw_item(entry: Mapping[str, Any]) -> RawItem | None:
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
