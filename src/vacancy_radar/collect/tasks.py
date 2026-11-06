"""Celery task for collection: a thin wrapper around `collect_if_due`.

Beat sends `fetch_source` for every source every few minutes. The checks in
`collect_if_due` decide whether a request is actually sent, exactly as for
the CLI, so running the task more often or twice at once never sends a request
the source's `min_interval` does not allow.
"""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import httpx
import structlog
from celery import Task
from redis import Redis

from vacancy_radar.collect.retry import is_blocked, retry_delay
from vacancy_radar.collect.service import CollectOutcome, collect_if_due, describe
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.sources import ADAPTERS, FeedError, SourceAdapter
from vacancy_radar.sources.http import build_client
from vacancy_radar.sources.ratelimit import RateLimitError, TokenBucket
from vacancy_radar.worker import COLLECT_EXPIRES_SECONDS, celery_app

logger = structlog.get_logger("vacancy_radar.collect")

FETCH_SOURCE = "collect.fetch_source"


@lru_cache
def get_rate_limiter() -> TokenBucket:
    """Process-wide token bucket on the Celery Redis."""
    redis_url = get_settings().redis_url
    if redis_url is None:
        raise RuntimeError("REDIS_URL is required for the rate limiter")
    return TokenBucket(Redis.from_url(str(redis_url)))


def collect(adapter: SourceAdapter, *, is_retry: bool) -> CollectOutcome:
    """Run `collect_if_due` with the worker's rate-limited client and a new session."""
    with (
        build_client(rate_limiter=get_rate_limiter()) as client,
        get_sessionmaker()() as session,
    ):
        return collect_if_due(
            adapter,
            client=client,
            session=session,
            now=datetime.now(UTC),
            disabled=get_settings().disabled_sources,
            is_retry=is_retry,
        )


@celery_app.task(bind=True, name=FETCH_SOURCE)
def fetch_source(self: Task[Any, Any], slug: str) -> dict[str, object]:
    """Collect one source if it is due, retrying transient failures.

    A source gets at most `adapter.max_retries` retries (DOU none, see its
    adapter). The last failure is raised, so the task ends as FAILURE.
    Returns the logged event and its fields.
    """
    adapter = ADAPTERS[slug]
    retries = self.request.retries
    try:
        outcome = collect(adapter, is_retry=retries > 0)
    except (httpx.HTTPError, FeedError, RateLimitError) as exc:
        delay = retry_delay(exc, retries) if retries < adapter.max_retries else None
        log_failure(slug, exc, retries=retries, retry_in=delay)
        if delay is None:
            raise
        raise self.retry(
            exc=exc,
            countdown=delay,
            max_retries=adapter.max_retries,
            expires=retry_expires(delay),
        ) from exc

    event, fields = describe(outcome)
    logger.info(event, **fields)
    return {"event": event, **fields}


def retry_expires(delay: int) -> datetime:
    """When an unpicked retry message is dropped, counted from its countdown.

    Without its own expiry a retry inherits the Beat message's, which can end
    before the countdown does, and the retry would be dropped unrun.
    """
    return datetime.now(UTC) + timedelta(seconds=delay + COLLECT_EXPIRES_SECONDS)


def log_failure(slug: str, exc: BaseException, *, retries: int, retry_in: int | None) -> None:
    logger.error(
        "collect.blocked" if is_blocked(exc) else "collect.failed",
        source=slug,
        error_type=type(exc).__name__,
        error=str(exc),
        retries=retries,
        retry_in=retry_in,
    )
