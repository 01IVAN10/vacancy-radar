"""Celery task for analytics: a thin wrapper around `refresh_views`.

Beat sends it every half hour. It has no retries: a failed refresh leaves
the previous rows in place, and the next run refreshes them.
"""

from typing import Final

import structlog

from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.worker import celery_app

logger = structlog.get_logger("vacancy_radar.analytics")

ANALYTICS_REFRESH: Final = "analytics.refresh"


@celery_app.task(name=ANALYTICS_REFRESH)
def refresh_task() -> dict[str, int]:
    with get_sessionmaker()() as session:
        jobs = refresh_views(session)
    logger.info("analytics.refreshed", jobs=jobs)
    return {"jobs": jobs}
