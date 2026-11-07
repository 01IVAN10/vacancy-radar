"""What the routes depend on: the web app's config, a database session, the signed-in user."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.auth.session import SignedInUser

# Opens a session for one unit of work: `async with sessions() as session`.
Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Settings the web app resolved once at startup, kept in `app.state.config`."""

    secret_key: str
    cookie_secure: bool
    sessions: Sessions
    # None until a bot is set up; the Telegram page then says linking is off.
    telegram_bot_username: str | None = None


def web_config(request: Request) -> WebConfig:
    config: WebConfig = request.app.state.config
    return config


Config = Annotated[WebConfig, Depends(web_config)]


async def get_session(config: Config) -> AsyncIterator[AsyncSession]:
    """One session per request. Routes and services commit what they change."""
    async with config.sessions() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def current_user(request: Request) -> SignedInUser | None:
    """The signed-in user, which `CurrentUserMiddleware` resolved for every request."""
    user: SignedInUser | None = request.state.user
    return user


OptionalUser = Annotated[SignedInUser | None, Depends(current_user)]


class LoginRequiredError(Exception):
    """Raised for a signed-out visitor; the app redirects them to sign in."""

    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


def require_user(request: Request, user: OptionalUser) -> SignedInUser:
    if user is None:
        raise LoginRequiredError(_page_path(request))
    return user


CurrentUser = Annotated[SignedInUser, Depends(require_user)]


def _page_path(request: Request) -> str:
    """The page to return to after sign-in.

    An HTMX request asks for a fragment, so its own URL is not a page. HTMX
    sends the URL of the page it was made from in `HX-Current-URL`. Only the
    path and query are kept, and sign-in checks them again with `safe_next`.
    """
    url = urlsplit(str(request.url))
    current = request.headers.get("HX-Current-URL")
    if request.headers.get("HX-Request") == "true" and current:
        url = urlsplit(current)
    path = url.path or "/"
    return f"{path}?{url.query}" if url.query else path
