"""CSRF protection with a double-submit cookie.

Every HTML response to a request without a `csrf_token` cookie sets one with
a random value. Templates put the same value into each form (`csrf_token`
field) and into the `X-CSRF-Token` header that HTMX sends. A state-changing
request passes only if the value it sends equals the cookie. Another site can
make the browser send the cookie, but cannot read it, so it cannot send the
matching value.

The session cookie is also `SameSite=Lax`, which already keeps it off
cross-site POSTs in current browsers. The token is the second, independent
layer, and it also covers the sign-in form, where there is no session yet.
"""

import hmac
import secrets
from typing import Final

from fastapi import HTTPException, Request, status
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSRF_COOKIE: Final = "csrf_token"
CSRF_FIELD: Final = "csrf_token"
CSRF_HEADER: Final = "X-CSRF-Token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, *, secure: bool) -> None:
    # httpOnly: templates get the value from the server, scripts never read it.
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="lax", secure=secure, path="/")


class CSRFCookieMiddleware:
    """Give each request a CSRF token in `request.state.csrf_token`.

    The token comes from the cookie. A request without it, or with an empty
    one, gets a new token, and an HTML response to it sets the cookie, so a
    form rendered for it can be submitted. Other responses, such as
    `/health`, are left without cookies.

    A plain ASGI middleware rather than `BaseHTTPMiddleware`, which runs the
    app in a separate task and buffers the response.
    """

    def __init__(self, app: ASGIApp, *, secure: bool) -> None:
        self.app = app
        self.secure = secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        existing = HTTPConnection(scope).cookies.get(CSRF_COOKIE)
        token = existing or new_csrf_token()
        scope.setdefault("state", {})["csrf_token"] = token
        if existing:
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if headers.get("content-type", "").startswith("text/html"):
                    headers.append("set-cookie", self._cookie_header(token))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def _cookie_header(self, token: str) -> str:
        # Built by `set_csrf_cookie`, so the attributes are written in one place.
        response = Response()
        set_csrf_cookie(response, token, secure=self.secure)
        return response.headers["set-cookie"]


async def verify_csrf(request: Request) -> None:
    """Dependency for every state-changing route: reject a missing or wrong token."""
    expected = request.cookies.get(CSRF_COOKIE)
    sent = request.headers.get(CSRF_HEADER)
    if sent is None:
        value = (await request.form()).get(CSRF_FIELD)
        sent = value if isinstance(value, str) else None
    if not expected or not sent or not hmac.compare_digest(expected.encode(), sent.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The form expired. Reload the page.")
