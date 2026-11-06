"""Celery task for reprocessing: a thin wrapper around `normalize_pending`.

Beat sends it every few minutes. It has no retries: a failed run leaves its
items pending, and the next scheduled run picks them up.
"""

from dataclasses import asdict

import structlog

from vacancy_radar.collect.tasks import get_rate_limiter
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.normalize.service import normalize_pending
from vacancy_radar.sources.http import build_client
from vacancy_radar.worker import celery_app

logger = structlog.get_logger("vacancy_radar.normalize")

NORMALIZE_PENDING = "normalize.pending"


@celery_app.task(name=NORMALIZE_PENDING)
def normalize_pending_task() -> dict[str, int]:
    """Normalize pending raw items with the worker's rate-limited client."""
    with build_client(rate_limiter=get_rate_limiter()) as client, get_sessionmaker()() as session:
        stats = normalize_pending(session, client)
    logger.info("normalize.finished", **asdict(stats))
    return asdict(stats)
