"""Session tokens: a JWT that names the signed-in user, kept in a cookie.

Tokens are signed with HS256 and live for `TOKEN_TTL`. There is no refresh
token and no server-side session list: signing out deletes the cookie, and a
token that leaked stays valid until it expires. `docs/auth.md` explains the
trade-off.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

import jwt

ALGORITHM: Final = "HS256"
TOKEN_TTL: Final = timedelta(days=7)


def issue_token(user_id: int, *, key: str, now: datetime | None = None) -> str:
    """A token for the user, valid for `TOKEN_TTL` from `now`."""
    issued_at = now or datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": issued_at, "exp": issued_at + TOKEN_TTL}
    return jwt.encode(claims, key, algorithm=ALGORITHM)


def read_token(token: str, *, key: str) -> int | None:
    """The user id from a valid token, or None for any invalid one.

    Invalid covers a bad signature, another algorithm (`none` included,
    because only HS256 is accepted), an expired or not yet valid token, a
    missing claim, and garbage.
    """
    try:
        claims = jwt.decode(
            token, key, algorithms=[ALGORITHM], options={"require": ["sub", "iat", "exp"]}
        )
    except jwt.InvalidTokenError:
        return None
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject.isdigit():
        return None
    return int(subject)
