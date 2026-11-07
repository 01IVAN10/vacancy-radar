"""Subscription storage on the async engine used by the web app.

Every query names the owner in its WHERE clause, so a subscription id from
another account behaves exactly like one that does not exist: reads return
None and writes change nothing. Routes turn both into 404.
"""

from typing import Any, Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.db.models import Subscription, User
from vacancy_radar.domain.subscriptions import SubscriptionDraft

MAX_SUBSCRIPTIONS: Final = 20


class SubscriptionLimitError(Exception):
    """The account already has `MAX_SUBSCRIPTIONS` subscriptions."""


async def list_for_user(session: AsyncSession, user_id: int) -> list[Subscription]:
    """The account's subscriptions, oldest first."""
    rows = await session.scalars(
        select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.id)
    )
    return list(rows)


async def get_for_user(
    session: AsyncSession, user_id: int, subscription_id: int
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id, Subscription.user_id == user_id
        )
    )


async def create(session: AsyncSession, user_id: int, draft: SubscriptionDraft) -> Subscription:
    """Store and commit a new subscription. Raises `SubscriptionLimitError`.

    The account row is locked while counting, so two requests at once cannot
    both pass the limit.
    """
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    count = await session.scalar(
        select(func.count()).select_from(Subscription).where(Subscription.user_id == user_id)
    )
    if count is not None and count >= MAX_SUBSCRIPTIONS:
        await session.rollback()
        raise SubscriptionLimitError
    subscription = Subscription(user_id=user_id, **_columns(draft))
    session.add(subscription)
    await session.commit()
    return subscription


async def update_for_user(
    session: AsyncSession, user_id: int, subscription_id: int, draft: SubscriptionDraft
) -> Subscription | None:
    """Replace the name and filter and commit, or return None if it is not the user's."""
    return await _update(session, user_id, subscription_id, _columns(draft))


async def toggle_active(
    session: AsyncSession, user_id: int, subscription_id: int
) -> Subscription | None:
    """Pause an active subscription or resume a paused one, in one statement."""
    return await _update(session, user_id, subscription_id, {"is_active": ~Subscription.is_active})


async def delete_for_user(session: AsyncSession, user_id: int, subscription_id: int) -> bool:
    """Delete and commit; False if there was no such subscription of the user's."""
    deleted = await session.scalar(
        delete(Subscription)
        .where(Subscription.id == subscription_id, Subscription.user_id == user_id)
        .returning(Subscription.id)
    )
    await session.commit()
    return deleted is not None


def _columns(draft: SubscriptionDraft) -> dict[str, Any]:
    criteria = draft.filter
    return {
        "name": draft.name,
        "skills": list(criteria.skills),
        "skills_match": criteria.skills_match,
        "seniority": list(criteria.seniority),
        "work_formats": list(criteria.work_formats),
        "min_salary_usd": criteria.min_salary_usd,
        "include_unsalaried": criteria.include_unsalaried,
    }


async def _update(
    session: AsyncSession, user_id: int, subscription_id: int, values: dict[str, Any]
) -> Subscription | None:
    subscription = await session.scalar(
        update(Subscription)
        .where(Subscription.id == subscription_id, Subscription.user_id == user_id)
        .values(**values, updated_at=func.now())
        .returning(Subscription)
        # Replace any copy of the row the session already holds.
        .execution_options(populate_existing=True)
    )
    await session.commit()
    return subscription
