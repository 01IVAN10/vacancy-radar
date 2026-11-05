"""Parsing shared by the RSS adapters. Pure: no I/O."""

from collections.abc import Callable, Mapping
from typing import Any

import feedparser
import structlog

from vacancy_radar.sources.base import FeedError, FetchResult, RawItem

logger = structlog.get_logger(__name__)

Entry = Mapping[str, Any]


def parse_rss(
    content: bytes, *, source: str, to_raw_item: Callable[[Entry], RawItem | None]
) -> FetchResult:
    """Turn an RSS document into raw items with the source's `to_raw_item`.

    HTML sanitizing and URI rewriting are off, so descriptions are stored
    exactly as the source sent them. They are sanitized when shown, not when
    stored. Entries that `to_raw_item` rejects are counted as skipped.
    """
    feed = feedparser.parse(content, sanitize_html=False, resolve_relative_uris=False)

    items: list[RawItem] = []
    skipped = 0
    for entry in feed.entries:
        item = to_raw_item(entry)
        if item is None:
            skipped += 1
            logger.warning("feed.item_skipped", source=source, link=entry.get("link"))
        else:
            items.append(item)

    # feedparser recovers from many XML errors; fail only if nothing usable is left.
    if feed.bozo:
        if not items:
            raise FeedError(f"{source} feed cannot be parsed: {feed.bozo_exception}")
        logger.warning("feed.malformed", source=source, error=str(feed.bozo_exception))

    return FetchResult(items=items, skipped=skipped)
