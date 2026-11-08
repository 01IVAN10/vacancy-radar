"""Deliver queued notifications to Telegram, at most once each.

A delivery is every `pending` row of one account for one cluster: a vacancy
that matched two subscriptions arrives as one message naming both.

**At most once.** A delivery is claimed (`sending`, one more attempt) and
committed before the request. A process that dies between the request and
storing its result leaves the rows `sending`, and they are never sent again.
For the same reason a request that may have reached Telegram before the
connection failed is not retried: a missed message is better than a
duplicate. Only failures that certainly sent nothing are retried.

| Failure | Rows become | The run |
|---|---|---|
| 429 | `pending` after `retry_after`; the attempt is not counted | stops |
| 5xx, no connection | `pending` after a backoff; `failed` after `MAX_ATTEMPTS` | stops |
| a timeout after the request was sent | `failed`, "outcome unknown" | goes on |
| 403: the bot was blocked | `failed`, and the chat is unlinked | goes on |
| 401: the token was rejected | `pending`; the attempt is not counted | raises |
| any other error | `failed` | goes on |
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from celery.utils.time import get_exponential_backoff_interval
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from vacancy_radar.db.models import Notification, Source, Subscription, User, Vacancy
from vacancy_radar.notify.message import VacancyCard, render_message
from vacancy_radar.telegram.client import (
    TelegramClient,
    TelegramError,
    TelegramForbiddenError,
    TelegramRateLimitedError,
    TelegramServerError,
    TelegramUnauthorizedError,
    TelegramUnavailableError,
)
from vacancy_radar.telegram.linking import unlink_chat

logger = structlog.get_logger("vacancy_radar.notify")

# A run sends at most this many messages; the next run picks up the rest.
# At one request a second, a full run takes under a minute.
MAX_DELIVERIES_PER_RUN: Final = 50
MAX_ATTEMPTS: Final = 5
# Backoff doubles from 30 s, capped at 10 min, with full jitter.
BACKOFF_FACTOR_SECONDS: Final = 30
BACKOFF_MAX_SECONDS: Final = 600

OUTCOME_UNKNOWN: Final = "outcome unknown"


@dataclass(frozen=True, slots=True)
class Delivery:
    """One claimed message: the rows it covers and what it shows."""

    notification_ids: tuple[int, ...]
    attempts: int
    chat_id: int | None
    card: VacancyCard
    subscription_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    # Set when the run stopped early: seconds until a retry is worth it.
    retry_in: int | None = None


def send_pending(
    session: Session,
    client: TelegramClient,
    *,
    site_url: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    limit: int = MAX_DELIVERIES_PER_RUN,
) -> SendResult:
    """Send due deliveries one by one until none is left, `limit` is reached or Telegram says stop.

    Commits after every claim and every result. Raises
    `TelegramUnauthorizedError` after putting the claimed rows back.
    """
    sent = failed = skipped = 0
    for _ in range(limit):
        delivery = claim_next(session, now=clock())
        if delivery is None:
            break
        if delivery.chat_id is None:
            _finish(session, delivery, status="skipped")
            skipped += 1
            continue
        html = render_message(delivery.card, delivery.subscription_names, site_url=site_url)
        try:
            message_id = client.send_message(delivery.chat_id, html)
        except TelegramRateLimitedError as exc:
            _release(session, delivery, after=exc.retry_after, now=clock(), count_attempt=False)
            return SendResult(sent, failed, skipped, retry_in=exc.retry_after)
        except TelegramUnauthorizedError:
            _release(session, delivery, after=0, now=clock(), count_attempt=False)
            raise
        except (TelegramServerError, TelegramUnavailableError) as exc:
            if isinstance(exc, TelegramUnavailableError) and exc.maybe_delivered:
                _finish(session, delivery, status="failed", error=f"{OUTCOME_UNKNOWN}: {exc}")
                failed += 1
                continue
            retry_in = _retry_later(session, delivery, exc, now=clock())
            if retry_in is not None:
                return SendResult(sent, failed, skipped, retry_in=retry_in)
            failed += 1
        except TelegramForbiddenError as exc:
            _finish(session, delivery, status="failed", error=str(exc))
            unlink_chat(session, delivery.chat_id)
            logger.warning("notify.chat_unlinked", reason=str(exc))
            failed += 1
        except TelegramError as exc:
            _finish(session, delivery, status="failed", error=str(exc))
            failed += 1
        else:
            _finish(session, delivery, status="sent", sent_at=clock(), message_id=message_id)
            sent += 1
    return SendResult(sent, failed, skipped)


def claim_next(session: Session, *, now: datetime) -> Delivery | None:
    """Claim the next due delivery and commit, or return None when nothing is due.

    The account's row is locked while claiming, with SKIP LOCKED, so two
    senders never split one delivery between them: whoever locks the
    account claims every pending row it has for that cluster.
    """
    due = session.execute(
        select(Subscription.user_id, Notification.cluster_id)
        .join(Subscription, Subscription.id == Notification.subscription_id)
        .join(User, User.id == Subscription.user_id)
        .where(Notification.status == "pending", Notification.next_attempt_at <= now)
        .order_by(Notification.next_attempt_at, Notification.id)
        .limit(1)
        .with_for_update(of=User, skip_locked=True)
    ).first()
    if due is None:
        session.rollback()
        return None
    user_id, cluster_id = due

    claimed = session.execute(
        update(Notification)
        .where(
            Notification.status == "pending",
            Notification.cluster_id == cluster_id,
            Notification.subscription_id.in_(
                select(Subscription.id).where(Subscription.user_id == user_id)
            ),
        )
        .values(status="sending", attempts=Notification.attempts + 1)
        .returning(Notification.id, Notification.vacancy_id, Notification.attempts)
    ).all()
    names = session.scalars(
        select(Subscription.name)
        .join(Notification, Notification.subscription_id == Subscription.id)
        .where(Notification.id.in_([row.id for row in claimed]))
        .order_by(Subscription.name)
    ).all()
    chat_id = session.scalar(select(User.telegram_chat_id).where(User.id == user_id))
    card = _card(session, min(row.vacancy_id for row in claimed))
    session.commit()
    return Delivery(
        notification_ids=tuple(row.id for row in claimed),
        attempts=max(row.attempts for row in claimed),
        chat_id=chat_id,
        card=card,
        subscription_names=tuple(names),
    )


def _card(session: Session, vacancy_id: int) -> VacancyCard:
    vacancy, source_name = session.execute(
        select(Vacancy, Source.name)
        .join(Source, Source.id == Vacancy.source_id)
        .where(Vacancy.id == vacancy_id)
    ).one()
    return VacancyCard(
        title=vacancy.title,
        url=vacancy.url,
        source_name=source_name,
        company=vacancy.company,
        cities=tuple(vacancy.cities),
        salary_min=vacancy.salary_min,
        salary_max=vacancy.salary_max,
        salary_currency=vacancy.salary_currency,
        salary_period=vacancy.salary_period,
        seniority=tuple(vacancy.seniority),
        work_formats=tuple(vacancy.work_formats),
        skills=tuple(vacancy.skills),
    )


def _retry_later(
    session: Session, delivery: Delivery, exc: TelegramError, *, now: datetime
) -> int | None:
    """Put a delivery that certainly was not sent back with a backoff.

    Returns the delay, or None when it has used up its attempts and failed.
    """
    if delivery.attempts >= MAX_ATTEMPTS:
        _finish(session, delivery, status="failed", error=str(exc))
        return None
    delay = backoff_seconds(delivery.attempts)
    _release(session, delivery, after=delay, now=now, count_attempt=True)
    return delay


def backoff_seconds(attempts: int) -> int:
    """Delay before the next attempt after `attempts` failed ones: full jitter, at least 1 s."""
    return max(
        1,
        get_exponential_backoff_interval(
            factor=BACKOFF_FACTOR_SECONDS,
            retries=attempts - 1,
            maximum=BACKOFF_MAX_SECONDS,
            full_jitter=True,
        ),
    )


def _release(
    session: Session, delivery: Delivery, *, after: int, now: datetime, count_attempt: bool
) -> None:
    attempts = Notification.attempts if count_attempt else Notification.attempts - 1
    _update(
        session,
        delivery.notification_ids,
        status="pending",
        attempts=attempts,
        next_attempt_at=now + timedelta(seconds=after),
    )


def _finish(
    session: Session,
    delivery: Delivery,
    *,
    status: str,
    error: str | None = None,
    sent_at: datetime | None = None,
    message_id: int | None = None,
) -> None:
    if status == "failed":
        logger.warning("notify.failed", notifications=delivery.notification_ids, error=error)
    _update(
        session,
        delivery.notification_ids,
        status=status,
        error=error,
        sent_at=sent_at,
        telegram_message_id=message_id,
    )


def _update(session: Session, ids: Sequence[int], **values: object) -> None:
    session.execute(update(Notification).where(Notification.id.in_(ids)).values(**values))
    session.commit()
