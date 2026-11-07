"""Registration and sign-in on the async engine used by the web app."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from vacancy_radar.auth.passwords import hash_password, verify_password, verify_unknown_account
from vacancy_radar.db.models import User
from vacancy_radar.domain.accounts import (
    MAX_PASSWORD_LENGTH,
    email_error,
    normalize_email,
    password_error,
)

EMAIL_TAKEN = "An account with this email already exists."


@dataclass
class RegistrationError(Exception):
    """The form cannot become an account. `errors` maps a field to its message."""

    errors: dict[str, str] = field(default_factory=dict)


async def register(session: AsyncSession, email: str, password: str) -> User:
    """Create and commit an account. Raises `RegistrationError`.

    A taken email is detected by the unique constraint rather than a lookup
    first, so two concurrent sign-ups with one email cannot both succeed.
    """
    email = normalize_email(email)
    errors = {
        name: message
        for name, message in (("email", email_error(email)), ("password", password_error(password)))
        if message is not None
    }
    if errors:
        raise RegistrationError(errors)

    user = User(email=email, password_hash=await run_in_threadpool(hash_password, password))
    try:
        async with session.begin_nested():
            session.add(user)
    except IntegrityError as exc:
        if _constraint(exc) == "uq_app_user_email":
            raise RegistrationError({"email": EMAIL_TAKEN}) from exc
        raise
    await session.commit()
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    """The account the email and password belong to, or None.

    Unknown emails and wrong passwords take about the same time and give the
    same result. A hash made with outdated parameters is replaced.
    """
    if len(password) > MAX_PASSWORD_LENGTH:
        return None  # no account can have it, so do not make the hasher work on it
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        await run_in_threadpool(verify_unknown_account, password)
        return None
    matches, new_hash = await run_in_threadpool(verify_password, password, user.password_hash)
    if not matches:
        return None
    if new_hash is not None:
        user.password_hash = new_hash
        await session.commit()
    return user


def _constraint(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
