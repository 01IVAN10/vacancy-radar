"""Subscription storage on the async engine used by the web app.

Every query names the owner in its WHERE clause, so a subscription id from
another account behaves exactly like one that does not exist: reads return
None and writes change nothing. Routes turn both into 404.

Callers get `SavedSubscription`s with a domain `SubscriptionFilter`, never
ORM rows. `filter_of` and `_write` are the one mapping between the two, and
matching in stage 7 reads filters through `filter_of` as well.
"""

from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.db.models import Subscription, User
from vacancy_radar.domain.subscriptions import SubscriptionDraft, SubscriptionFilter

MAX_SUBSCRIPTIONS: Final = 20


@dataclass(frozen=True, slots=True)
class SavedSubscription:
    id: int
    name: str
    filter: SubscriptionFilter
    is_active: bool


class SubscriptionLimitError(Exception):
    """The account already has `MAX_SUBSCRIPTIONS` subscriptions."""


def filter_of(row: Subscription) -> SubscriptionFilter:
    """The domain filter a stored row holds."""
    return SubscriptionFilter.from_stored(
        skills=row.skills,
        skills_match=row.skills_match,
        seniority=row.seniority,
        work_formats=row.work_formats,
        min_salary_usd=row.min_salary_usd,
        include_unsalaried=row.include_unsalaried,
    )


async def count_for_user(session: AsyncSession, user_id: int) -> int:
    count = await session.scalar(
        select(func.count()).select_from(Subscription).where(Subscription.user_id == user_id)
    )
    return count or 0


async def list_for_user(session: AsyncSession, user_id: int) -> list[SavedSubscription]:
    """The account's subscriptions, oldest first."""
    rows = await session.scalars(
        select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.id)
    )
    return [_saved(row) for row in rows]


async def get_for_user(
    session: AsyncSession, user_id: int, subscription_id: int
) -> SavedSubscription | None:
    row = await _owned(session, user_id, subscription_id)
    return None if row is None else _saved(row)


async def create(
    session: AsyncSession, user_id: int, draft: SubscriptionDraft
) -> SavedSubscription:
    """Store and commit a new subscription. Raises `SubscriptionLimitError`.

    The account row is locked while counting, so two requests at once cannot
    both pass the limit.
    """
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    if await count_for_user(session, user_id) >= MAX_SUBSCRIPTIONS:
        await session.rollback()
        raise SubscriptionLimitError
    row = Subscription(user_id=user_id)
    _write(row, draft)
    session.add(row)
    await session.commit()
    return _saved(row)


async def update_for_user(
    session: AsyncSession, user_id: int, subscription_id: int, draft: SubscriptionDraft
) -> SavedSubscription | None:
    """Replace the name and filter and commit, or return None if it is not the user's."""
    row = await _owned(session, user_id, subscription_id, for_update=True)
    if row is None:
        return None
    _write(row, draft)
    await session.commit()
    return _saved(row)


async def toggle_active(
    session: AsyncSession, user_id: int, subscription_id: int
) -> SavedSubscription | None:
    """Pause an active subscription or resume a paused one.

    The row is locked first, so two toggles at once pause and then resume
    instead of both flipping the same state.
    """
    row = await _owned(session, user_id, subscription_id, for_update=True)
    if row is None:
        return None
    row.is_active = not row.is_active
    await session.commit()
    return _saved(row)


async def delete_for_user(session: AsyncSession, user_id: int, subscription_id: int) -> bool:
    """Delete and commit; False if there was no such subscription of the user's."""
    row = await _owned(session, user_id, subscription_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def _owned(
    session: AsyncSession, user_id: int, subscription_id: int, *, for_update: bool = False
) -> Subscription | None:
    query = select(Subscription).where(
        Subscription.id == subscription_id, Subscription.user_id == user_id
    )
    if for_update:
        query = query.with_for_update()
    # Reload the row even if the session holds it, so a lock sees current values.
    return await session.scalar(query.execution_options(populate_existing=True))


def _write(row: Subscription, draft: SubscriptionDraft) -> None:
    criteria = draft.filter
    row.name = draft.name
    row.skills = list(criteria.skills)
    row.skills_match = criteria.skills_match
    row.seniority = list(criteria.seniority)
    row.work_formats = list(criteria.work_formats)
    row.min_salary_usd = None if criteria.salary is None else criteria.salary.min_usd
    row.include_unsalaried = None if criteria.salary is None else criteria.salary.include_unsalaried


def _saved(row: Subscription) -> SavedSubscription:
    return SavedSubscription(
        id=row.id, name=row.name, filter=filter_of(row), is_active=row.is_active
    )
