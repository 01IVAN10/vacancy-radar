from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tests.support import TEST_SECRET_KEY
from vacancy_radar.api.deps import WebConfig
from vacancy_radar.api.middleware import CurrentUserMiddleware
from vacancy_radar.auth.csrf import CSRFCookieMiddleware

pytestmark = pytest.mark.anyio


def no_sessions() -> AbstractAsyncContextManager[AsyncSession]:
    raise AssertionError("a lifespan event must not open a database session")


async def receive() -> Message:
    return {"type": "lifespan.startup"}


async def send(message: Message) -> None:
    pass


@pytest.mark.parametrize(
    "middleware",
    [
        lambda app: CurrentUserMiddleware(
            app,
            config=WebConfig(
                TEST_SECRET_KEY,
                cookie_secure=False,
                sessions=no_sessions,
                telegram_bot_username=None,
            ),
        ),
        lambda app: CSRFCookieMiddleware(app, secure=False),
    ],
    ids=["current_user", "csrf"],
)
async def test_middlewares_pass_lifespan_events_through(
    middleware: Callable[[ASGIApp], ASGIApp],
) -> None:
    seen: list[Scope] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    scope: Scope = {"type": "lifespan"}
    await middleware(app)(scope, receive, send)

    assert seen == [{"type": "lifespan"}]
