"""Celery application for background workers and the Beat scheduler.

Collection and reprocessing stay independent of Celery: the tasks in
`vacancy_radar.collect.tasks` and `vacancy_radar.normalize.tasks` are thin
wrappers around plain functions.
"""

from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from celery import Celery, signals

from vacancy_radar.config import get_settings
from vacancy_radar.log import configure_logging
from vacancy_radar.sources import ADAPTERS, SourceAdapter

# How often Beat offers each source to the worker. Each source's
# `min_interval` decides whether a request is sent, so this only sets how
# soon after becoming due a source is fetched (Djinni every 25 to 30 min,
# DOU every 30 to 35 min).
COLLECT_EVERY = timedelta(minutes=5)
# A message the worker has not picked up by then is dropped instead of piling
# up while the worker is down. The next Beat run replaces it.
COLLECT_EXPIRES_SECONDS = 240
# How often Beat sends the reprocessing task. Each run handles only pending
# raw items, so most runs find nothing to do and finish at once.
NORMALIZE_EVERY = timedelta(minutes=10)
NORMALIZE_EXPIRES_SECONDS = 540


def build_beat_schedule(adapters: Iterable[SourceAdapter]) -> dict[str, dict[str, Any]]:
    """The reprocessing task, and one `fetch_source` entry per source.

    Separate entries per source mean a slow source never delays another.
    """
    schedule: dict[str, dict[str, Any]] = {
        f"fetch-{adapter.slug}": {
            "task": "collect.fetch_source",
            "schedule": COLLECT_EVERY,
            "args": (adapter.slug,),
            "options": {"expires": COLLECT_EXPIRES_SECONDS},
        }
        for adapter in adapters
    }
    schedule["normalize-pending"] = {
        "task": "normalize.pending",
        "schedule": NORMALIZE_EVERY,
        "options": {"expires": NORMALIZE_EXPIRES_SECONDS},
    }
    return schedule


def create_celery() -> Celery:
    """Build the Celery app with Redis as both broker and result backend."""
    redis_url = get_settings().redis_url
    if redis_url is None:
        raise RuntimeError("REDIS_URL is required for the Celery worker and Beat")
    app = Celery(
        "vacancy_radar",
        broker=str(redis_url),
        backend=str(redis_url),
        include=["vacancy_radar.collect.tasks", "vacancy_radar.normalize.tasks"],
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        timezone="UTC",
        beat_schedule=build_beat_schedule(ADAPTERS.values()),
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery()


@signals.worker_init.connect
def setup_structlog(**_: object) -> None:
    """Configure structlog in the worker; forked pool processes inherit it."""
    configure_logging(get_settings().log_level)


@celery_app.task
def ping() -> str:
    """Health probe: proves a worker can pick up and run a task."""
    return "pong"
