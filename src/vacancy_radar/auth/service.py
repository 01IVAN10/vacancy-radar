"""Registration and sign-in on the async engine used by the web app."""

from collections.abc import Mapping
from typing import Final

from psycopg import errors as pg_errors
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from vacancy_radar.auth.passwords import hash_password, verify_password, verify_unknown_account
from vacancy_radar.db.models import User
from vacancy_radar.domain.accounts import (
    INVALID_EMAIL,
    MAX_PASSWORD_LENGTH,
    email_error,
    normalize_email,
    password_error,
)

EMAIL_TAKEN: Final = "An account with this email already exists."


class RegistrationError(Exception):
    """The form cannot become an account. `errors` maps a field to its message."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        if not errors:
            raise ValueError("a RegistrationError needs at least one field error")
        self.errors = dict(errors)
        super().__init__("; ".join(f"{field}: {message}" for field, message in self.errors.items()))


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
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        error = _registration_error(exc)
        if error is None:
            raise
        raise error from exc
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


def _registration_error(exc: IntegrityError) -> RegistrationError | None:
    """The form error for a constraint the new account violated, if it is ours."""
    orig = exc.orig
    if isinstance(orig, pg_errors.UniqueViolation) and orig.diag.constraint_name == (
        "uq_app_user_email"
    ):
        return RegistrationError({"email": EMAIL_TAKEN})
    # Python and Postgres lower-case by different Unicode versions, so a few
    # letters Python leaves alone are upper case to Postgres (U+A7CB on
    # Postgres 16 with glibc 2.41, for one). Such an email is refused rather
    # than stored in a form that sign-in could not match.
    if isinstance(orig, pg_errors.CheckViolation) and orig.diag.constraint_name == (
        "ck_app_user_email_lowercase"
    ):
        return RegistrationError({"email": INVALID_EMAIL})
    return None
