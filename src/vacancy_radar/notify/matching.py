"""Queue the new clusters that match each subscription as `notification` rows.

A cluster is a candidate for a subscription when all of these hold:

- the subscription is active and its account has a linked chat;
- the cluster was first seen after both the subscription became active and
  the chat was linked (the watermark), so neither creating a subscription
  nor linking a chat sends what was collected before;
- it was first seen within `LOOKBACK`, which bounds the work of each run;
- the vacancy is ready: its salary, if it has one in hryvnias or euros, has
  been converted to dollars, or `CONVERSION_GRACE` has passed since it was
  normalized. Otherwise a salary not yet converted would read as no salary;
- no notification exists yet for the subscription and the cluster.

The decision itself is `domain.matching.matches`. A candidate that does not
match is looked at again by every run within the lookback, so a vacancy whose
salary is converted later can still match.
"""

from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vacancy_radar.db.models import Notification, Subscription, User, Vacancy, VacancyCluster
from vacancy_radar.domain.matching import VacancyFacts, matches
from vacancy_radar.domain.subscriptions import SubscriptionFilter
from vacancy_radar.subscriptions.service import filter_of

LOOKBACK: Final = timedelta(days=2)
CONVERSION_GRACE: Final = timedelta(hours=1)


def match_new(session: Session, *, now: datetime) -> int:
    """Queue every matching candidate as `pending`. Returns how many rows were added.

    Does not commit. The insert skips pairs that are already queued, so a
    rerun, or two runs at once, never queues a cluster twice.
    """
    filters: dict[int, SubscriptionFilter] = {}
    queued: list[dict[str, object]] = []
    for subscription, vacancy in session.execute(_candidates(now)).all():
        criteria = filters.get(subscription.id)
        if criteria is None:
            criteria = filters[subscription.id] = filter_of(subscription)
        facts = VacancyFacts.from_stored(
            skills=vacancy.skills,
            seniority=vacancy.seniority,
            work_formats=vacancy.work_formats,
            salary_usd_min=vacancy.salary_usd_min,
            salary_usd_max=vacancy.salary_usd_max,
        )
        if matches(criteria, facts):
            queued.append(
                {
                    "subscription_id": subscription.id,
                    "cluster_id": vacancy.cluster_id,
                    "vacancy_id": vacancy.id,
                    "status": "pending",
                    "next_attempt_at": now,
                }
            )
    if not queued:
        return 0
    added = session.scalars(
        insert(Notification)
        .values(queued)
        .on_conflict_do_nothing(
            index_elements=[Notification.subscription_id, Notification.cluster_id]
        )
        .returning(Notification.id)
    ).all()
    return len(added)


def _candidates(now: datetime) -> Select[Subscription, Vacancy]:
    already_queued = exists().where(
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
        select(Subscription, Vacancy)
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
        .where(Subscription.is_active, User.telegram_chat_id.is_not(None), ready, ~already_queued)
        .order_by(VacancyCluster.first_seen_at, Vacancy.id, Subscription.id)
    )
