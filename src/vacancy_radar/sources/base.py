"""What every source adapter provides, independent of how it is scheduled.

An adapter only fetches and parses. Storage lives in `vacancy_radar.collect`,
so the CLI and (from stage 4) the Celery task share the same code path.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class RawItem:
    """One vacancy as the source returned it.

    `payload` holds the item's fields unchanged and must be JSON-serializable,
    because it is stored as JSONB in `raw_vacancy`.
    """

    external_id: str
    url: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Items parsed from one fetch, plus how many could not be identified."""

    items: list[RawItem]
    skipped: int = 0


class FeedError(Exception):
    """The source answered, but its response cannot be parsed."""


class SourceAdapter(Protocol):
    """A site vacancies are collected from."""

    slug: str
    name: str
    base_url: str

    def fetch(self, client: httpx.Client) -> FetchResult:
        """Fetch the source once.

        Raises `httpx.HTTPError` on network and HTTP errors, `FeedError` on a
        response that cannot be parsed.
        """
        ...
