from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from vacancy_radar.db.models import TelegramLinkToken, User
from vacancy_radar.telegram.linking import (
    LINK_TOKEN_TTL,
    Linked,
    deep_link,
    hash_token,
    issue_link_token,
    link_chat,
    new_token,
    unlink_chat,
    unlink_user,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CHAT = 123456789


def add_user(session: Session, email: str = "ivan@example.com") -> User:
    user = User(email=email, password_hash="x")
    session.add(user)
    session.flush()
    return user


def add_token(session: Session, user: User, *, expires_at: datetime | None = None) -> str:
    token = new_token()
    session.add(
        TelegramLinkToken(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=expires_at or NOW + LINK_TOKEN_TTL,
        )
    )
    session.flush()
    return token


def reloaded(session: Session, user: User) -> User:
    session.refresh(user)
    return user


# --- tokens and links -----------------------------------------------------------


def test_tokens_are_random_and_fit_a_deep_link() -> None:
    tokens = {new_token() for _ in range(100)}

    assert len(tokens) == 100
    # Telegram allows up to 64 characters from [A-Za-z0-9_-] after `start=`.
    assert all(
        len(token) <= 64 and token.replace("-", "").replace("_", "").isalnum() for token in tokens
    )


def test_only_the_hash_is_stored() -> None:
    token = new_token()

    assert hash_token(token) != token
    assert len(hash_token(token)) == 64
    assert hash_token(token) == hash_token(token)


def test_deep_link() -> None:
    assert deep_link("vacancy_radar_bot", "abc-DEF_123") == (
        "https://t.me/vacancy_radar_bot?start=abc-DEF_123"
    )


# --- web side (async) -------------------------------------------------------------


async def add_async_user(session: AsyncSession, email: str = "ivan@example.com") -> User:
    user = User(email=email, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def stored_tokens(session: AsyncSession) -> list[TelegramLinkToken]:
    rows = await session.scalars(
        select(TelegramLinkToken).execution_options(populate_existing=True)
    )
    return list(rows)


@pytest.mark.anyio
async def test_issuing_stores_the_hash_with_an_expiry(async_session: AsyncSession) -> None:
    user = await add_async_user(async_session)

    token = await issue_link_token(async_session, user.id, now=NOW)

    [stored] = await stored_tokens(async_session)
    assert (stored.token_hash, stored.user_id, stored.expires_at) == (
        hash_token(token),
        user.id,
        NOW + LINK_TOKEN_TTL,
    )


@pytest.mark.anyio
async def test_a_new_token_replaces_the_previous_one(async_session: AsyncSession) -> None:
    user = await add_async_user(async_session)
    await issue_link_token(async_session, user.id, now=NOW)

    second = await issue_link_token(async_session, user.id, now=NOW + timedelta(minutes=1))

    assert [row.token_hash for row in await stored_tokens(async_session)] == [hash_token(second)]


@pytest.mark.anyio
async def test_unlinking_from_the_site(async_session: AsyncSession) -> None:
    user = await add_async_user(async_session)
    user.telegram_chat_id, user.telegram_linked_at = CHAT, NOW
    await async_session.commit()

    await unlink_user(async_session, user.id)

    stored = (
        await async_session.execute(
            select(User.telegram_chat_id, User.telegram_linked_at).where(User.id == user.id)
        )
    ).one()
    assert tuple(stored) == (None, None)


# --- bot side (sync) ----------------------------------------------------------------


def test_a_token_links_the_chat_to_its_account(db_session: Session) -> None:
    user = add_user(db_session)
    token = add_token(db_session, user)

    assert link_chat(db_session, token, CHAT, now=NOW) == Linked(
        email="ivan@example.com", moved_from_another_account=False
    )

    linked = reloaded(db_session, user)
    assert (linked.telegram_chat_id, linked.telegram_linked_at) == (CHAT, NOW)


def test_a_token_works_once(db_session: Session) -> None:
    user = add_user(db_session)
    token = add_token(db_session, user)
    link_chat(db_session, token, CHAT, now=NOW)

    assert link_chat(db_session, token, 987654321, now=NOW) is None
    assert reloaded(db_session, user).telegram_chat_id == CHAT
    assert db_session.scalars(select(TelegramLinkToken)).all() == []


def test_an_expired_token_links_nothing_and_is_deleted(db_session: Session) -> None:
    user = add_user(db_session)
    token = add_token(db_session, user, expires_at=NOW)

    assert link_chat(db_session, token, CHAT, now=NOW) is None
    assert reloaded(db_session, user).telegram_chat_id is None
    assert db_session.scalars(select(TelegramLinkToken)).all() == []


def test_an_unknown_token_links_nothing(db_session: Session) -> None:
    user = add_user(db_session)
    add_token(db_session, user)

    assert link_chat(db_session, "made-up", CHAT, now=NOW) is None
    assert reloaded(db_session, user).telegram_chat_id is None


def test_a_chat_linked_to_another_account_moves(db_session: Session) -> None:
    first = add_user(db_session, "first@example.com")
    second = add_user(db_session, "second@example.com")
    link_chat(db_session, add_token(db_session, first), CHAT, now=NOW)

    assert link_chat(db_session, add_token(db_session, second), CHAT, now=NOW) == Linked(
        email="second@example.com", moved_from_another_account=True
    )
    assert reloaded(db_session, first).telegram_chat_id is None
    assert reloaded(db_session, first).telegram_linked_at is None
    assert reloaded(db_session, second).telegram_chat_id == CHAT


def test_linking_another_chat_replaces_the_old_one(db_session: Session) -> None:
    user = add_user(db_session)
    link_chat(db_session, add_token(db_session, user), CHAT, now=NOW)
    later = NOW + timedelta(days=1)

    token = add_token(db_session, user, expires_at=later + LINK_TOKEN_TTL)
    link_chat(db_session, token, 987654321, now=later)

    linked = reloaded(db_session, user)
    assert (linked.telegram_chat_id, linked.telegram_linked_at) == (987654321, later)


def test_unlinking_a_chat(db_session: Session) -> None:
    user = add_user(db_session)
    link_chat(db_session, add_token(db_session, user), CHAT, now=NOW)

    assert unlink_chat(db_session, CHAT)
    assert not unlink_chat(db_session, CHAT)
    linked = reloaded(db_session, user)
    assert (linked.telegram_chat_id, linked.telegram_linked_at) == (None, None)


def test_a_chat_belongs_to_one_account(db_session: Session) -> None:
    first = add_user(db_session, "first@example.com")
    first.telegram_chat_id, first.telegram_linked_at = CHAT, NOW
    db_session.flush()
    second = add_user(db_session, "second@example.com")
    second.telegram_chat_id, second.telegram_linked_at = CHAT, NOW

    with pytest.raises(IntegrityError, match="uq_app_user_telegram_chat_id"):
        db_session.flush()


def test_a_chat_needs_its_link_time(db_session: Session) -> None:
    user = add_user(db_session)
    user.telegram_chat_id = CHAT

    with pytest.raises(IntegrityError, match="ck_app_user_telegram_linked"):
        db_session.flush()


def test_deleting_an_account_deletes_its_token(db_session: Session) -> None:
    user = add_user(db_session)
    add_token(db_session, user)

    db_session.delete(user)
    db_session.flush()

    assert db_session.scalars(select(TelegramLinkToken)).all() == []
