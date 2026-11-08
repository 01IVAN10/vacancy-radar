"""Send due deliveries to Telegram, at most once each.

**At most once.** A delivery is claimed (`sending`, one more attempt) and
committed before the request. A worker that dies between the request and
storing its result leaves it `sending`, and it is never sent again. What a
failed send means is decided by `notify.outcome.outcome_of`; this module only
applies it.

**One sender at a time.** `sender_lock` holds a Postgres advisory lock for a
whole run, so a 429 that stops one run is not ignored by another running
beside it. Claims also use SKIP LOCKED, so even two senders would never take
the same delivery.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from vacancy_radar.db.models import Delivery, Notification, Source, Subscription, User, Vacancy
from vacancy_radar.domain.delivery import DeliveryStatus
from vacancy_radar.notify.message import VacancyCard, render_message
from vacancy_radar.notify.outcome import Outcome, outcome_of
from vacancy_radar.telegram.client import TelegramClient
from vacancy_radar.telegram.linking import unlink_chat

logger = structlog.get_logger("vacancy_radar.notify")

# A run handles at most this many deliveries (sent, failed or skipped); the
# next run picks up the rest. At one request a second it fits in a minute,
# the interval Beat sends `notify.send` at.
MAX_DELIVERIES_PER_RUN: Final = 50

# First key of the two-key advisory lock; collection uses 1.
NOTIFY_LOCK_NAMESPACE: Final = 2


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    """A delivery claimed for sending, with what its message shows."""

    id: int
    attempts: int
    chat_id: int | None
    card: VacancyCard
    subscription_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


@contextmanager
def sender_lock(engine: Engine) -> Iterator[bool]:
    """Yield True if this process may send now, holding the lock until the block ends.

    The lock lives on a connection of its own, because the sending session
    commits, and a session-level lock must be released on the connection
    that took it.
    """
    with engine.connect() as connection:
        locked = bool(
            connection.scalar(select(func.pg_try_advisory_lock(NOTIFY_LOCK_NAMESPACE, 0)))
        )
        try:
            yield locked
        finally:
            if locked:
                connection.scalar(select(func.pg_advisory_unlock(NOTIFY_LOCK_NAMESPACE, 0)))
            connection.rollback()


def send_pending(
    session: Session,
    client: TelegramClient,
    *,
    site_url: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    limit: int = MAX_DELIVERIES_PER_RUN,
) -> SendResult:
    """Send due deliveries one by one, until none is due, `limit` is reached or the run stops.

    Commits after every claim and every result. Re-raises the exceptions
    `outcome_of` says to, after storing the delivery's outcome.
    """
    sent = failed = skipped = 0
    for _ in range(limit):
        claim = claim_next(session, now=clock())
        if claim is None:
            break
        if claim.chat_id is None:
            _store(session, claim.id, status="skipped")
            skipped += 1
            continue
        try:
            html = render_message(claim.card, claim.subscription_names, site_url=site_url)
            message_id = client.send_message(claim.chat_id, html)
        except Exception as exc:
            outcome = outcome_of(exc, attempts=claim.attempts)
            _apply(session, claim, outcome, now=clock())
            failed += outcome.status == "failed"
            if outcome.reraise:
                raise
            if outcome.stop_run:
                break
            continue
        _store(session, claim.id, status="sent", sent_at=clock(), telegram_message_id=message_id)
        sent += 1
    return SendResult(sent, failed, skipped)


def claim_next(session: Session, *, now: datetime) -> ClaimedDelivery | None:
    """Claim the next due delivery and commit, or return None when nothing is due."""
    delivery_id = session.scalar(
        select(Delivery.id)
        .where(Delivery.status == "pending", Delivery.next_attempt_at <= now)
        .order_by(Delivery.next_attempt_at, Delivery.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if delivery_id is None:
        session.rollback()
        return None
    attempts, user_id, vacancy_id = session.execute(
        update(Delivery)
        .where(Delivery.id == delivery_id)
        .values(status="sending", attempts=Delivery.attempts + 1)
        .returning(Delivery.attempts, Delivery.user_id, Delivery.vacancy_id)
    ).one()
    names = session.scalars(
        select(Subscription.name)
        .join(Notification, Notification.subscription_id == Subscription.id)
        .where(Notification.delivery_id == delivery_id)
        .order_by(Subscription.name)
    ).all()
    chat_id = session.scalar(select(User.telegram_chat_id).where(User.id == user_id))
    card = _card(session, vacancy_id)
    session.commit()
    return ClaimedDelivery(delivery_id, attempts, chat_id, card, tuple(names))


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


def _apply(session: Session, claim: ClaimedDelivery, outcome: Outcome, *, now: datetime) -> None:
    logger.warning(
        "notify.send_failed",
        delivery=claim.id,
        status=outcome.status,
        retry_in=outcome.retry_in if outcome.status == "pending" else None,
        error=outcome.error,
    )
    if outcome.unlink_chat and claim.chat_id is not None:
        unlink_chat(session, claim.chat_id)
        logger.warning("notify.chat_unlinked", delivery=claim.id, reason=outcome.error)
    if outcome.status == "pending":
        _store(
            session,
            claim.id,
            status="pending",
            error=outcome.error,
            next_attempt_at=now + timedelta(seconds=outcome.retry_in),
            attempts=Delivery.attempts if outcome.count_attempt else Delivery.attempts - 1,
        )
    else:
        _store(session, claim.id, status="failed", error=outcome.error)


def _store(session: Session, delivery_id: int, *, status: DeliveryStatus, **values: object) -> None:
    session.execute(
        update(Delivery).where(Delivery.id == delivery_id).values(status=status, **values)
    )
    session.commit()
