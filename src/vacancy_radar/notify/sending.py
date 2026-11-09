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

The message shows the cluster's vacancy with the lowest id, chosen when the
delivery is claimed, so it always shows a vacancy that still exists.
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
from vacancy_radar.notify.message import VacancyCard, render_message
from vacancy_radar.notify.outcome import GiveUp, Outcome, Retry, outcome_of
from vacancy_radar.telegram.client import TelegramClient
from vacancy_radar.telegram.linking import forget_chat

logger = structlog.get_logger("vacancy_radar.notify")

# A run handles at most this many deliveries (sent, failed or skipped); the
# next run picks up the rest. At one request a second it fits in a minute,
# the interval Beat sends `notify.send` at.
MAX_DELIVERIES_PER_RUN: Final = 50

# First key of the two-key advisory lock; collection uses 1.
NOTIFY_LOCK_NAMESPACE: Final = 2

NO_VACANCY: Final = "the cluster has no vacancy left"
# Far longer than a run (at most 50 requests at one a second).
STUCK_AFTER: Final = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    """A delivery claimed for sending, with what its message shows.

    `card` is None when the cluster has no vacancy left to show.
    """

    id: int
    attempts: int
    chat_id: int | None
    card: VacancyCard | None
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
        if claim.chat_id is None or claim.card is None:
            _skip(session, claim.id, error=None if claim.card else NO_VACANCY)
            skipped += 1
            continue
        try:
            html = render_message(claim.card, claim.subscription_names, site_url=site_url)
            message_id = client.send_message(claim.chat_id, html)
        except Exception as exc:
            outcome = outcome_of(exc, attempts=claim.attempts)
            _apply(session, claim, outcome, now=clock())
            failed += isinstance(outcome.delivery, GiveUp)
            if outcome.run == "raise":
                raise
            if outcome.run == "stop":
                break
            continue
        _mark_sent(session, claim.id, sent_at=clock(), message_id=message_id)
        sent += 1
    return SendResult(sent, failed, skipped)


def count_stuck(session: Session, *, now: datetime) -> int:
    """Deliveries left `sending` for longer than any run lasts: their worker died mid-send."""
    return (
        session.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(Delivery.status == "sending", Delivery.claimed_at < now - STUCK_AFTER)
        )
        or 0
    )


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
    attempts, user_id, cluster_id = session.execute(
        update(Delivery)
        .where(Delivery.id == delivery_id)
        .values(status="sending", attempts=Delivery.attempts + 1, claimed_at=now)
        .returning(Delivery.attempts, Delivery.user_id, Delivery.cluster_id)
    ).one()
    names = session.scalars(
        select(Subscription.name)
        .join(Notification, Notification.subscription_id == Subscription.id)
        .where(Notification.delivery_id == delivery_id)
        .order_by(Subscription.name)
    ).all()
    chat_id = session.scalar(select(User.telegram_chat_id).where(User.id == user_id))
    card = _card(session, cluster_id)
    session.commit()
    return ClaimedDelivery(delivery_id, attempts, chat_id, card, tuple(names))


def _card(session: Session, cluster_id: int) -> VacancyCard | None:
    found = session.execute(
        select(Vacancy, Source.name)
        .join(Source, Source.id == Vacancy.source_id)
        .where(Vacancy.cluster_id == cluster_id)
        .order_by(Vacancy.id)
        .limit(1)
    ).first()
    if found is None:
        return None
    vacancy, source_name = found
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
    """Store the outcome of a failed send, the chat unlinking included, in one transaction."""
    match outcome.delivery:
        case Retry(after=after, count_attempt=count_attempt):
            logger.warning(
                "notify.send_failed", delivery=claim.id, retry_in=after, error=outcome.error
            )
            session.execute(
                update(Delivery)
                .where(Delivery.id == claim.id)
                .values(
                    status="pending",
                    error=outcome.error,
                    next_attempt_at=now + timedelta(seconds=after),
                    attempts=Delivery.attempts if count_attempt else Delivery.attempts - 1,
                )
            )
        case GiveUp(unlink_chat=unlink_chat):
            logger.warning("notify.send_failed", delivery=claim.id, error=outcome.error)
            if unlink_chat and claim.chat_id is not None:
                forget_chat(session, claim.chat_id, via="blocked")
            session.execute(
                update(Delivery)
                .where(Delivery.id == claim.id)
                .values(status="failed", error=outcome.error)
            )
    session.commit()


def _mark_sent(session: Session, delivery_id: int, *, sent_at: datetime, message_id: int) -> None:
    session.execute(
        update(Delivery)
        .where(Delivery.id == delivery_id)
        .values(status="sent", sent_at=sent_at, telegram_message_id=message_id)
    )
    session.commit()


def _skip(session: Session, delivery_id: int, *, error: str | None) -> None:
    session.execute(
        update(Delivery).where(Delivery.id == delivery_id).values(status="skipped", error=error)
    )
    session.commit()
