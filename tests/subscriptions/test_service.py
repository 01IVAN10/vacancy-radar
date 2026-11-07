from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import add_user
from vacancy_radar.db.models import Subscription, User
from vacancy_radar.domain.subscriptions import SalaryFloor, SubscriptionDraft, SubscriptionFilter
from vacancy_radar.subscriptions import service
from vacancy_radar.subscriptions.service import (
    MAX_SUBSCRIPTIONS,
    SavedSubscription,
    SubscriptionLimitError,
    filter_of,
)

pytestmark = pytest.mark.anyio

PYTHON_REMOTE = SubscriptionFilter(
    skills=("python", "django"),
    skills_match="all",
    seniority=(),
    work_formats=("remote",),
    salary=SalaryFloor(3000, include_unsalaried=False),
)


def draft(name: str = "Python remote", **criteria: Any) -> SubscriptionDraft:
    return SubscriptionDraft(name=name, filter=replace(PYTHON_REMOTE, **criteria))


@pytest.fixture
async def owner(async_session: AsyncSession, password_hash: str) -> User:
    return await add_user(async_session, password_hash, "owner@example.com")


@pytest.fixture
async def stranger(async_session: AsyncSession, password_hash: str) -> User:
    return await add_user(async_session, password_hash, "stranger@example.com")


async def row_of(async_session: AsyncSession, saved: SavedSubscription) -> Subscription:
    row = await async_session.get(Subscription, saved.id, populate_existing=True)
    assert row is not None
    return row


async def test_create_stores_the_filter_and_reads_it_back(
    async_session: AsyncSession, owner: User
) -> None:
    created = await service.create(async_session, owner.id, draft())

    assert created == SavedSubscription(
        id=created.id, name="Python remote", filter=PYTHON_REMOTE, is_active=True
    )
    assert await service.get_for_user(async_session, owner.id, created.id) == created
    row = await row_of(async_session, created)
    assert (row.min_salary_usd, row.include_unsalaried) == (3000, False)


async def test_a_filter_without_salary_stores_neither_salary_column(
    async_session: AsyncSession, owner: User
) -> None:
    created = await service.create(async_session, owner.id, draft(salary=None))

    row = await row_of(async_session, created)
    assert (row.min_salary_usd, row.include_unsalaried) == (None, None)
    assert filter_of(row).salary is None


async def test_list_shows_only_the_owners_subscriptions_oldest_first(
    async_session: AsyncSession, owner: User, stranger: User
) -> None:
    first = await service.create(async_session, owner.id, draft("first"))
    await service.create(async_session, stranger.id, draft("theirs"))
    second = await service.create(async_session, owner.id, draft("second"))

    assert await service.list_for_user(async_session, owner.id) == [first, second]
    assert await service.count_for_user(async_session, owner.id) == 2


async def test_another_users_subscription_cannot_be_read_or_changed(
    async_session: AsyncSession, owner: User, stranger: User
) -> None:
    theirs = await service.create(async_session, owner.id, draft("theirs"))

    assert await service.get_for_user(async_session, stranger.id, theirs.id) is None
    assert await service.update_for_user(async_session, stranger.id, theirs.id, draft("x")) is None
    assert await service.toggle_active(async_session, stranger.id, theirs.id) is None
    assert await service.delete_for_user(async_session, stranger.id, theirs.id) is False

    assert await service.get_for_user(async_session, owner.id, theirs.id) == theirs


async def test_update_replaces_name_and_filter(async_session: AsyncSession, owner: User) -> None:
    created = await service.create(async_session, owner.id, draft())
    senior = SubscriptionFilter(
        skills=(), skills_match="any", seniority=("senior",), work_formats=(), salary=None
    )

    updated = await service.update_for_user(
        async_session, owner.id, created.id, SubscriptionDraft("Senior anything", senior)
    )

    assert updated == SavedSubscription(created.id, "Senior anything", senior, is_active=True)
    row = await row_of(async_session, created)
    assert row.updated_at >= row.created_at
    assert (row.min_salary_usd, row.include_unsalaried) == (None, None)


async def test_toggle_pauses_and_resumes(async_session: AsyncSession, owner: User) -> None:
    created = await service.create(async_session, owner.id, draft())

    paused = await service.toggle_active(async_session, owner.id, created.id)
    resumed = await service.toggle_active(async_session, owner.id, created.id)

    assert paused is not None
    assert paused.is_active is False
    assert resumed is not None
    assert resumed.is_active is True


async def test_delete_removes_the_subscription(async_session: AsyncSession, owner: User) -> None:
    created = await service.create(async_session, owner.id, draft())

    assert await service.delete_for_user(async_session, owner.id, created.id) is True
    assert await service.get_for_user(async_session, owner.id, created.id) is None
    assert await service.delete_for_user(async_session, owner.id, created.id) is False


async def test_create_stops_at_the_limit(async_session: AsyncSession, owner: User) -> None:
    # The refused create rolls back, which expires ORM objects such as `owner`.
    owner_id = owner.id
    for number in range(MAX_SUBSCRIPTIONS):
        await service.create(async_session, owner_id, draft(f"#{number}"))

    with pytest.raises(SubscriptionLimitError):
        await service.create(async_session, owner_id, draft("one too many"))

    assert await service.count_for_user(async_session, owner_id) == MAX_SUBSCRIPTIONS


async def test_deleting_an_account_deletes_its_subscriptions(
    async_session: AsyncSession, owner: User
) -> None:
    await service.create(async_session, owner.id, draft())

    await async_session.delete(owner)
    await async_session.commit()

    assert await async_session.scalar(select(func.count()).select_from(Subscription)) == 0
