import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import TEST_BOT_USERNAME, add_user, csrf_token, sign_in
from vacancy_radar.api.deps import Sessions
from vacancy_radar.config import get_settings
from vacancy_radar.db.models import TelegramLinkToken, User
from vacancy_radar.telegram.linking import hash_token

pytestmark = pytest.mark.anyio

LINKED_AT = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
DEEP_LINK = re.compile(rf'href="https://t\.me/{TEST_BOT_USERNAME}\?start=([A-Za-z0-9_-]+)"')


@pytest.fixture
async def owner(client: AsyncClient, async_session: AsyncSession, password_hash: str) -> User:
    user = await add_user(async_session, password_hash)
    sign_in(client, user)
    return user


async def link(async_session: AsyncSession, user: User) -> None:
    user.telegram_chat_id, user.telegram_linked_at = 123456789, LINKED_AT
    await async_session.commit()


async def test_signed_out_visitors_are_sent_to_sign_in(client: AsyncClient) -> None:
    response = await client.get("/telegram")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Ftelegram"


@pytest.mark.usefixtures("owner")
async def test_an_unlinked_account_is_offered_a_link(client: AsyncClient) -> None:
    response = await client.get("/telegram")

    assert response.status_code == 200
    assert "No chat is linked yet." in response.text
    assert "Link Telegram</button>" in response.text
    assert "Unlink" not in response.text


async def test_linking_shows_a_one_time_deep_link(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    response = await client.post("/telegram/link", data={"csrf_token": await csrf_token(client)})

    assert response.status_code == 200
    found = DEEP_LINK.search(response.text)
    assert found is not None
    stored = await async_session.scalar(select(TelegramLinkToken))
    assert stored is not None
    assert (stored.user_id, stored.token_hash) == (owner.id, hash_token(found.group(1)))
    assert "works once, for 15 minutes" in response.text
    # Reloading this page would send the POST again and replace the token.
    assert 'Pressed Start? <a href="/telegram">Check the status</a>' in response.text
    assert "reload" not in response.text.lower()


@pytest.mark.usefixtures("owner")
async def test_linking_needs_the_csrf_token(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    await csrf_token(client)

    response = await client.post("/telegram/link")

    assert response.status_code == 403
    assert await async_session.scalar(select(TelegramLinkToken)) is None


async def test_a_linked_account_sees_since_when_and_can_unlink(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await link(async_session, owner)

    response = await client.get("/telegram")

    assert "A chat is linked since" in response.text
    assert "08 Nov 2026, 12:00 UTC" in response.text
    assert "Link another chat</button>" in response.text
    assert "Unlink</button>" in response.text


async def test_the_link_time_is_shown_in_utc_whatever_the_session_time_zone(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await link(async_session, owner)
    # Times come back in the session's zone: 14:00+02:00 here.
    await async_session.execute(text("SET TIME ZONE 'Europe/Kyiv'"))

    response = await client.get("/telegram")

    assert "08 Nov 2026, 12:00 UTC" in response.text
    assert 'datetime="2026-11-08T12:00:00+00:00"' in response.text


async def test_unlinking(client: AsyncClient, async_session: AsyncSession, owner: User) -> None:
    await link(async_session, owner)

    response = await client.post("/telegram/unlink", data={"csrf_token": await csrf_token(client)})

    assert response.status_code == 303
    assert response.headers["location"] == "/telegram"
    await async_session.refresh(owner)
    assert (owner.telegram_chat_id, owner.telegram_linked_at) == (None, None)


async def test_unlinking_needs_the_csrf_token(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await link(async_session, owner)
    await csrf_token(client)

    assert (await client.post("/telegram/unlink")).status_code == 403
    await async_session.refresh(owner)
    assert owner.telegram_chat_id == 123456789


@pytest.mark.usefixtures("owner")
async def test_the_subscriptions_page_asks_to_link_telegram(client: AsyncClient) -> None:
    response = await client.get("/subscriptions")

    assert "No Telegram chat is linked" in response.text
    assert 'href="/telegram"' in response.text


async def test_the_subscriptions_page_says_nothing_once_linked(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await link(async_session, owner)

    response = await client.get("/subscriptions")

    assert "No Telegram chat is linked" not in response.text


# --- without a bot ----------------------------------------------------------------


@pytest.fixture
async def unconfigured(test_sessions: Sessions) -> AsyncIterator[AsyncClient]:
    from vacancy_radar.api.app import create_app

    settings = get_settings().model_copy(update={"telegram_bot_username": None})
    app: FastAPI = create_app(settings, sessions=test_sessions)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def test_without_a_bot_the_page_says_linking_is_off(
    unconfigured: AsyncClient, async_session: AsyncSession, password_hash: str
) -> None:
    sign_in(unconfigured, await add_user(async_session, password_hash))

    page = await unconfigured.get("/telegram")
    linked = await unconfigured.post(
        "/telegram/link", data={"csrf_token": await csrf_token(unconfigured)}
    )

    assert "Telegram is not set up on this site yet" in page.text
    assert "<button" not in page.text.split("<main", 1)[1]
    assert linked.status_code == 503
    assert await async_session.scalar(select(TelegramLinkToken)) is None
