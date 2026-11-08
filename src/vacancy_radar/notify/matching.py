"""Record the new clusters that match each subscription, and queue one delivery per account.

A cluster is a candidate for a subscription when all of these hold:

- the subscription is active and its account has a linked chat;
- the cluster was first seen after both the subscription became active and
  the chat was linked (the watermark), so neither creating a subscription
  nor linking a chat sends what was collected before;
- it was first seen within `LOOKBACK`, which bounds the work of each run;
- the vacancy is ready: its salary, if it has one in hryvnias or euros, has
  been converted to dollars, or `CONVERSION_GRACE` has passed since it was
  normalized. Otherwise a salary not yet converted would read as no salary;
- the subscription has no `notification` for the cluster yet.

The decision itself is `domain.matching.matches`. Each match becomes a
`notification` row, attached to the account's `delivery` for the cluster,
which is created on the first match. A subscription that matches after the
message went out joins the delivery that was sent and sends nothing.

A candidate that does not match is looked at again by every run within the
lookback, so a vacancy whose salary is converted later can still match.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import batched
from typing import Final

from sqlalchemy import Select, and_, exists, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vacancy_radar.db.models import (
    Delivery,
    Notification,
    Subscription,
    User,
    Vacancy,
    VacancyCluster,
)
from vacancy_radar.domain.matching import VacancyFacts, matches
from vacancy_radar.domain.subscriptions import SubscriptionFilter
from vacancy_radar.subscriptions.service import filter_of

LOOKBACK: Final = timedelta(days=2)
CONVERSION_GRACE: Final = timedelta(hours=1)
# Rows per INSERT, well under Postgres's 65 535 bind parameters.
BATCH_SIZE: Final = 1000


@dataclass(frozen=True, slots=True)
class Match:
    subscription_id: int
    user_id: int
    cluster_id: int
    vacancy_id: int


@dataclass(frozen=True, slots=True)
class MatchResult:
    # New `notification` rows: one per subscription and cluster.
    matched: int = 0
    # New `delivery` rows: messages to send.
    queued: int = 0


def match_new(session: Session, *, now: datetime) -> MatchResult:
    """Record new matches and queue their deliveries as `pending`. Does not commit.

    Both inserts skip rows that already exist, so a rerun, or two runs at
    once, never records a match or queues a delivery twice.
    """
    found = list(_matches(session, now=now))
    queued = sum(_queue_deliveries(session, batch, now=now) for batch in batched(found, BATCH_SIZE))
    matched = sum(_record_matches(session, batch) for batch in batched(found, BATCH_SIZE))
    return MatchResult(matched=matched, queued=queued)


def _matches(session: Session, *, now: datetime) -> Iterator[Match]:
    filters: dict[int, SubscriptionFilter] = {
        row.id: filter_of(row)
        for row in session.scalars(
            select(Subscription)
            .join(User, User.id == Subscription.user_id)
            .where(Subscription.is_active, User.telegram_chat_id.is_not(None))
        )
    }
    if not filters:
        return
    for row in session.execute(_candidates(now).execution_options(yield_per=BATCH_SIZE)):
        facts = VacancyFacts.from_stored(
            skills=row.skills,
            seniority=row.seniority,
            work_formats=row.work_formats,
            salary_usd_min=row.salary_usd_min,
            salary_usd_max=row.salary_usd_max,
        )
        if matches(filters[row.subscription_id], facts):
            yield Match(row.subscription_id, row.user_id, row.cluster_id, row.vacancy_id)


def _candidates(
    now: datetime,
) -> Select[int, int, int, int, list[str], list[str], list[str], int | None, int | None]:
    already_matched = exists().where(
        Notification.subscription_id == Subscription.id,
        Notification.cluster_id == VacancyCluster.id,
    )
    ready = or_(
        Vacancy.salary_currency.is_(None),
        Vacancy.salary_period == "hour",
        Vacancy.salary_usd_min.is_not(None),
        Vacancy.salary_usd_max.is_not(None),
        Vacancy.normalized_at <= now - CONVERSION_GRACE,
    )
    return (
        select(
            Subscription.id.label("subscription_id"),
            Subscription.user_id,
            VacancyCluster.id.label("cluster_id"),
            Vacancy.id.label("vacancy_id"),
            Vacancy.skills,
            Vacancy.seniority,
            Vacancy.work_formats,
            Vacancy.salary_usd_min,
            Vacancy.salary_usd_max,
        )
        .join(User, User.id == Subscription.user_id)
        .join(
            VacancyCluster,
            and_(
                VacancyCluster.first_seen_at
                >= func.greatest(Subscription.active_since, User.telegram_linked_at),
                VacancyCluster.first_seen_at >= now - LOOKBACK,
            ),
        )
        .join(Vacancy, Vacancy.cluster_id == VacancyCluster.id)
        .where(Subscription.is_active, User.telegram_chat_id.is_not(None), ready, ~already_matched)
        .order_by(VacancyCluster.first_seen_at, Vacancy.id, Subscription.id)
    )


def _queue_deliveries(session: Session, batch: Sequence[Match], *, now: datetime) -> int:
    """Create the deliveries the batch needs. Returns how many were new."""
    first_of: dict[tuple[int, int], Match] = {}
    for match in batch:
        first_of.setdefault((match.user_id, match.cluster_id), match)
    added = session.scalars(
        insert(Delivery)
        .values(
            [
                {
                    "user_id": match.user_id,
                    "cluster_id": match.cluster_id,
                    "vacancy_id": match.vacancy_id,
                    "status": "pending",
                    "next_attempt_at": now,
                }
                for match in first_of.values()
            ]
        )
        .on_conflict_do_nothing(index_elements=[Delivery.user_id, Delivery.cluster_id])
        .returning(Delivery.id)
    ).all()
    return len(added)


def _record_matches(session: Session, batch: Sequence[Match]) -> int:
    """Record the batch as `notification` rows on their deliveries. Returns how many were new."""
    pairs = {(match.user_id, match.cluster_id) for match in batch}
    delivery_of = {
        (user_id, cluster_id): delivery_id
        for delivery_id, user_id, cluster_id in session.execute(
            select(Delivery.id, Delivery.user_id, Delivery.cluster_id).where(
                tuple_(Delivery.user_id, Delivery.cluster_id).in_(pairs)
            )
        )
    }
    added = session.scalars(
        insert(Notification)
        .values(
            [
                {
                    "subscription_id": match.subscription_id,
                    "cluster_id": match.cluster_id,
                    "delivery_id": delivery_of[match.user_id, match.cluster_id],
                }
                for match in batch
            ]
        )
        .on_conflict_do_nothing(
            index_elements=[Notification.subscription_id, Notification.cluster_id]
        )
        .returning(Notification.id)
    ).all()
    return len(added)
