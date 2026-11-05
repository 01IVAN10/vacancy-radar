"""Source adapters, keyed by the slug stored in the `source` table."""

from vacancy_radar.sources.base import FeedError, FetchResult, RawItem, SourceAdapter
from vacancy_radar.sources.djinni import DjinniAdapter
from vacancy_radar.sources.dou import DouAdapter

_ADAPTERS: list[SourceAdapter] = [DjinniAdapter(), DouAdapter()]
ADAPTERS: dict[str, SourceAdapter] = {adapter.slug: adapter for adapter in _ADAPTERS}

__all__ = ["ADAPTERS", "FeedError", "FetchResult", "RawItem", "SourceAdapter"]
