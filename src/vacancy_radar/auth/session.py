"""The session cookie: who is signed in, and how that is written and erased.

Setting and clearing the cookie go through one function, because a browser
replaces a cookie only when name, path and domain match: a sign-out written
with other attributes than the sign-in would silently leave the user signed in.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from starlette.responses import Response

from vacancy_radar.auth.tokens import TOKEN_TTL, issue_token, read_token

SESSION_COOKIE: Final = "access_token"


@dataclass(frozen=True, slots=True)
class SignedInUser:
    """The account a request is signed in as, detached from any database session.

    `telegram_linked_at` is when a Telegram chat was linked to the account,
    or None without one. Pages show it from here, so none has to query it.
    """

    id: int
    email: str
    telegram_linked_at: datetime | None


def set_session_cookie(response: Response, user_id: int, *, key: str, secure: bool) -> None:
    token = issue_token(user_id, key=key)
    _write(response, token, max_age=int(TOKEN_TTL.total_seconds()), secure=secure)


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    _write(response, "", max_age=0, secure=secure)


def user_id_from(cookies: Mapping[str, str], *, key: str) -> int | None:
    """The user id of a valid session cookie, or None without one."""
    token = cookies.get(SESSION_COOKIE)
    return None if token is None else read_token(token, key=key)


def _write(response: Response, value: str, *, max_age: int, secure: bool) -> None:
    # HttpOnly keeps the token from scripts; Lax keeps it off cross-site POSTs.
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
