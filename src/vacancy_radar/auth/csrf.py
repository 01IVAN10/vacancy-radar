"""CSRF protection with a double-submit cookie.

Every HTML response that lacks the `csrf_token` cookie gets one with a
random value. Templates put the same value into each form (`csrf_token`
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
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

CSRF_COOKIE: Final = "csrf_token"
CSRF_FIELD: Final = "csrf_token"
CSRF_HEADER: Final = "X-CSRF-Token"


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, *, secure: bool) -> None:
    # httpOnly: templates get the value from the server, scripts never read it.
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="lax", secure=secure, path="/")


class CSRFCookieMiddleware(BaseHTTPMiddleware):
    """Give each request a CSRF token in `request.state.csrf_token`.

    The token comes from the cookie. A request without the cookie gets a new
    token, and an HTML response to it sets the cookie, so a form rendered
    for it can be submitted.
    """

    def __init__(self, app: ASGIApp, *, secure: bool) -> None:
        super().__init__(app)
        self.secure = secure

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        existing = request.cookies.get(CSRF_COOKIE)
        request.state.csrf_token = existing or new_csrf_token()
        response = await call_next(request)
        is_html = response.headers.get("content-type", "").startswith("text/html")
        if existing is None and is_html and not _sets_cookie(response, CSRF_COOKIE):
            set_csrf_cookie(response, request.state.csrf_token, secure=self.secure)
        return response


async def verify_csrf(request: Request) -> None:
    """Dependency for every state-changing route: reject a missing or wrong token."""
    expected = request.cookies.get(CSRF_COOKIE)
    sent = request.headers.get(CSRF_HEADER)
    if sent is None:
        value = (await request.form()).get(CSRF_FIELD)
        sent = value if isinstance(value, str) else None
    if not expected or not sent or not hmac.compare_digest(expected.encode(), sent.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The form expired. Reload the page.")


def _sets_cookie(response: Response, name: str) -> bool:
    prefix = f"{name}=".encode()
    return any(
        key == b"set-cookie" and value.startswith(prefix) for key, value in response.raw_headers
    )
