"""Resolve the signed-in user once per request, for routes and templates alike."""

from sqlalchemy import select
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from vacancy_radar.api.deps import WebConfig
from vacancy_radar.auth.session import SignedInUser, user_id_from
from vacancy_radar.db.models import User


class CurrentUserMiddleware:
    """Put the signed-in user, or None, into `request.state.user`.

    Doing it here rather than in a route dependency means every response,
    error pages included, renders the navigation for the right person, and no
    route can forget to ask. A missing, expired or forged token and a deleted
    account all mean signed out. Requests without a session cookie, such as
    health checks, do not touch the database.
    """

    def __init__(self, app: ASGIApp, *, config: WebConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["user"] = await self._load(HTTPConnection(scope))
        await self.app(scope, receive, send)

    async def _load(self, connection: HTTPConnection) -> SignedInUser | None:
        user_id = user_id_from(connection.cookies, key=self.config.secret_key)
        if user_id is None:
            return None
        async with self.config.sessions() as session:
            row = (
                await session.execute(select(User.id, User.email).where(User.id == user_id))
            ).one_or_none()
        return None if row is None else SignedInUser(id=row.id, email=row.email)
