"""Celery tasks for notifications: thin wrappers around the plain functions.

`notify.match` clusters new vacancies (`clusters.service`) and records the
matches; if another run is clustering, it matches what is clustered already.
It also warns (`notify.stuck_deliveries`) about deliveries a dead worker left
`sending`. `notify.send` sends the due deliveries; Beat sends it every minute.
It has no Celery retries: a delivery that has to wait carries its own
`next_attempt_at`, and the first run after that sends it. One run sends at a
time (`sender_lock`).
"""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Final

import structlog

from vacancy_radar.clusters.service import assign_clusters
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_engine, get_sessionmaker
from vacancy_radar.notify.matching import match_new
from vacancy_radar.notify.sending import SendResult, count_stuck, send_pending, sender_lock
from vacancy_radar.ratelimit import get_rate_limiter
from vacancy_radar.telegram.client import TelegramClient, TelegramUnauthorizedError, build_http
from vacancy_radar.worker import celery_app

logger = structlog.get_logger("vacancy_radar.notify")

NOTIFY_MATCH: Final = "notify.match"
NOTIFY_SEND: Final = "notify.send"


@celery_app.task(name=NOTIFY_MATCH)
def match_task() -> dict[str, int]:
    """Cluster new vacancies, then record their matches and queue deliveries."""
    with get_sessionmaker()() as session:
        clusters = assign_clusters(session)
        session.commit()
        now = datetime.now(UTC)
        result = match_new(session, now=now)
        session.commit()
        stuck = count_stuck(session, now=now)
    if clusters.busy:
        logger.info("clusters.busy")
    summary = {"opened": clusters.opened, "joined": clusters.joined, **asdict(result)}
    logger.info("notify.matched", **summary)
    if stuck:
        # Left `sending` by a worker that died mid-send; never sent again.
        logger.warning("notify.stuck_deliveries", count=stuck)
    return summary


@celery_app.task(name=NOTIFY_SEND)
def send_task() -> dict[str, int]:
    """Send due deliveries with the worker's rate-limited client, unless another run is sending."""
    settings = get_settings()
    if settings.telegram_bot_token is None:
        logger.warning("telegram.not_configured", missing="TELEGRAM_BOT_TOKEN")
        return asdict(SendResult())
    with sender_lock(get_engine()) as locked:
        if not locked:
            logger.info("notify.send_busy")
            return asdict(SendResult())
        with build_http(rate_limiter=get_rate_limiter()) as http, get_sessionmaker()() as session:
            client = TelegramClient(http, settings.telegram_bot_token.get_secret_value())
            try:
                result = send_pending(session, client, site_url=settings.site_url)
            except TelegramUnauthorizedError as exc:
                logger.error("telegram.token_rejected", error=str(exc))
                raise
    logger.info("notify.sent", **asdict(result))
    return asdict(result)
