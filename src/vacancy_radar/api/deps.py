"""Dependencies shared by the routes: the database session and the signed-in user."""

from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.auth.tokens import read_token, signing_key
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_async_sessionmaker
from vacancy_radar.db.models import User

SESSION_COOKIE: Final = "access_token"


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request. Routes and services commit what they change."""
    async with get_async_sessionmaker()() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


async def current_user(request: Request, session: DbSession) -> User | None:
    """The user the session cookie names, or None.

    A missing, expired or forged token and a deleted account all mean
    "signed out". The user is also kept in `request.state.user`, so every
    template can show the navigation for them.
    """
    request.state.user = None
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        return None
    user_id = read_token(token, key=signing_key(get_settings()))
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    request.state.user = user
    return user


OptionalUser = Annotated[User | None, Depends(current_user)]


class LoginRequiredError(Exception):
    """Raised for a signed-out visitor; the app redirects them to sign in."""

    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


async def require_user(request: Request, user: OptionalUser) -> User:
    if user is None:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        raise LoginRequiredError(path)
    return user


CurrentUser = Annotated[User, Depends(require_user)]
