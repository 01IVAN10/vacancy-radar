"""Celery tasks for notifications: thin wrappers around the plain functions.

`notify.match` clusters new vacancies and queues the matches, then sends
`notify.send` at once when it queued any. Beat also sends `notify.send` on its
own schedule, which picks up deliveries that wait for a retry.

`notify.send` retries itself after a 429 or a transient failure, with the
delay `send_pending` reports. A delay above `RETRY_CAP_SECONDS` is left to the
next Beat run: the rows' `next_attempt_at` keeps them from being sent early.
"""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog
from celery import Task

from vacancy_radar.collect.tasks import get_rate_limiter
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.notify.clusters import assign_clusters
from vacancy_radar.notify.matching import match_new
from vacancy_radar.notify.sending import send_pending
from vacancy_radar.telegram.client import TelegramClient, TelegramUnauthorizedError, build_http
from vacancy_radar.worker import NOTIFY_EXPIRES_SECONDS, celery_app

logger = structlog.get_logger("vacancy_radar.notify")

NOTIFY_MATCH: Final = "notify.match"
NOTIFY_SEND: Final = "notify.send"
MAX_RETRIES: Final = 3
RETRY_CAP_SECONDS: Final = 600


@celery_app.task(name=NOTIFY_MATCH)
def match_task() -> dict[str, int]:
    """Cluster new vacancies, queue the matches, and send them if there are new ones."""
    with get_sessionmaker()() as session:
        clustered = assign_clusters(session)
        session.commit()
        queued = match_new(session, now=datetime.now(UTC))
        session.commit()
    logger.info("notify.matched", clustered=clustered, queued=queued)
    if queued:
        send_task.delay()
    return {"clustered": clustered, "queued": queued}


@celery_app.task(bind=True, name=NOTIFY_SEND)
def send_task(self: Task[Any, Any]) -> dict[str, int | None]:
    """Send due deliveries with the worker's rate-limited client."""
    settings = get_settings()
    if settings.telegram_bot_token is None:
        logger.warning("telegram.not_configured", missing="TELEGRAM_BOT_TOKEN")
        return {"sent": 0, "failed": 0, "skipped": 0, "retry_in": None}
    with (
        build_http(rate_limiter=get_rate_limiter()) as http,
        get_sessionmaker()() as session,
    ):
        client = TelegramClient(http, settings.telegram_bot_token.get_secret_value())
        try:
            result = send_pending(session, client, site_url=settings.site_url)
        except TelegramUnauthorizedError as exc:
            logger.error("telegram.token_rejected", error=str(exc))
            raise
    logger.info("notify.sent", **asdict(result))
    delay = result.retry_in
    if delay is not None and delay <= RETRY_CAP_SECONDS and self.request.retries < MAX_RETRIES:
        raise self.retry(countdown=delay, max_retries=MAX_RETRIES, expires=retry_expires(delay))
    return asdict(result)


def retry_expires(delay: int) -> datetime:
    """When an unpicked retry is dropped: after its countdown plus the usual expiry."""
    return datetime.now(UTC) + timedelta(seconds=delay + NOTIFY_EXPIRES_SECONDS)
