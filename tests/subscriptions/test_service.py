from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import add_user
from vacancy_radar.db.models import Subscription, User
from vacancy_radar.domain.subscriptions import SubscriptionDraft, SubscriptionFilter
from vacancy_radar.subscriptions import service
from vacancy_radar.subscriptions.service import MAX_SUBSCRIPTIONS, SubscriptionLimitError

pytestmark = pytest.mark.anyio


PYTHON_REMOTE = SubscriptionFilter(
    skills=("python",),
    skills_match="any",
    seniority=(),
    work_formats=("remote",),
    min_salary_usd=None,
    include_unsalaried=True,
)


def draft(name: str = "Python remote", **criteria: Any) -> SubscriptionDraft:
    return SubscriptionDraft(name=name, filter=replace(PYTHON_REMOTE, **criteria))


@pytest.fixture
async def owner(async_session: AsyncSession, password_hash: str) -> User:
    return await add_user(async_session, password_hash, "owner@example.com")


@pytest.fixture
async def stranger(async_session: AsyncSession, password_hash: str) -> User:
    return await add_user(async_session, password_hash, "stranger@example.com")


async def test_create_stores_the_draft(async_session: AsyncSession, owner: User) -> None:
    created = await service.create(
        async_session, owner.id, draft(min_salary_usd=3000, include_unsalaried=False)
    )

    stored = await service.get_for_user(async_session, owner.id, created.id)
    assert stored is not None
    assert (stored.name, stored.skills, stored.work_formats) == (
        "Python remote",
        ["python"],
        ["remote"],
    )
    assert (stored.min_salary_usd, stored.include_unsalaried, stored.is_active) == (
        3000,
        False,
        True,
    )


async def test_list_shows_only_the_owners_subscriptions_oldest_first(
    async_session: AsyncSession, owner: User, stranger: User
) -> None:
    first = await service.create(async_session, owner.id, draft("first"))
    await service.create(async_session, stranger.id, draft("theirs"))
    second = await service.create(async_session, owner.id, draft("second"))

    listed = await service.list_for_user(async_session, owner.id)

    assert [subscription.id for subscription in listed] == [first.id, second.id]


async def test_another_users_subscription_cannot_be_read_or_changed(
    async_session: AsyncSession, owner: User, stranger: User
) -> None:
    theirs = await service.create(async_session, owner.id, draft("theirs"))

    assert await service.get_for_user(async_session, stranger.id, theirs.id) is None
    assert await service.update_for_user(async_session, stranger.id, theirs.id, draft("x")) is None
    assert await service.toggle_active(async_session, stranger.id, theirs.id) is None
    assert await service.delete_for_user(async_session, stranger.id, theirs.id) is False

    await async_session.refresh(theirs)
    assert (theirs.name, theirs.is_active) == ("theirs", True)


async def test_update_replaces_name_and_filter(async_session: AsyncSession, owner: User) -> None:
    created = await service.create(async_session, owner.id, draft())

    updated = await service.update_for_user(
        async_session,
        owner.id,
        created.id,
        draft("Senior anything", skills=(), work_formats=(), seniority=("senior",)),
    )

    assert updated is not None
    assert (updated.name, updated.skills, updated.seniority) == ("Senior anything", [], ["senior"])
    assert updated.updated_at >= created.created_at


async def test_toggle_pauses_and_resumes(async_session: AsyncSession, owner: User) -> None:
    created = await service.create(async_session, owner.id, draft())

    paused = await service.toggle_active(async_session, owner.id, created.id)
    assert paused is not None
    assert paused.is_active is False
    resumed = await service.toggle_active(async_session, owner.id, created.id)
    assert resumed is not None
    assert resumed.is_active is True


async def test_delete_removes_the_subscription(async_session: AsyncSession, owner: User) -> None:
    created = await service.create(async_session, owner.id, draft())

    assert await service.delete_for_user(async_session, owner.id, created.id) is True
    assert await service.get_for_user(async_session, owner.id, created.id) is None
    assert await service.delete_for_user(async_session, owner.id, created.id) is False


async def test_create_stops_at_the_limit(async_session: AsyncSession, owner: User) -> None:
    for number in range(MAX_SUBSCRIPTIONS):
        await service.create(async_session, owner.id, draft(f"#{number}"))

    with pytest.raises(SubscriptionLimitError):
        await service.create(async_session, owner.id, draft("one too many"))

    count = await async_session.scalar(select(func.count()).select_from(Subscription))
    assert count == MAX_SUBSCRIPTIONS


async def test_deleting_an_account_deletes_its_subscriptions(
    async_session: AsyncSession, owner: User
) -> None:
    await service.create(async_session, owner.id, draft())

    await async_session.delete(owner)
    await async_session.commit()

    assert await async_session.scalar(select(func.count()).select_from(Subscription)) == 0
