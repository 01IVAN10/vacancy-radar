import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import PASSWORD, add_user
from vacancy_radar.auth.passwords import verify_password
from vacancy_radar.auth.service import EMAIL_TAKEN, RegistrationError, authenticate, register
from vacancy_radar.db.models import User

pytestmark = pytest.mark.anyio


async def test_register_stores_normalized_email_and_a_hash(async_session: AsyncSession) -> None:
    user = await register(async_session, "  Ivan@Example.com ", PASSWORD)

    stored = await async_session.get(User, user.id)
    assert stored is not None
    assert stored.email == "ivan@example.com"
    assert stored.password_hash != PASSWORD
    assert verify_password(PASSWORD, stored.password_hash)[0]


async def test_register_reports_every_invalid_field(async_session: AsyncSession) -> None:
    with pytest.raises(RegistrationError) as caught:
        await register(async_session, "not an email", "short")

    assert set(caught.value.errors) == {"email", "password"}


async def test_register_rejects_a_taken_email_in_any_case(
    async_session: AsyncSession, password_hash: str
) -> None:
    await add_user(async_session, password_hash, "ivan@example.com")

    with pytest.raises(RegistrationError) as caught:
        await register(async_session, "IVAN@example.com", PASSWORD)

    assert caught.value.errors == {"email": EMAIL_TAKEN}
    # The failed insert rolled back only its savepoint; the session is still usable.
    assert await async_session.scalar(select(func.count()).select_from(User)) == 1


async def test_authenticate_returns_the_account_for_the_right_password(
    async_session: AsyncSession, password_hash: str
) -> None:
    user = await add_user(async_session, password_hash, "ivan@example.com")

    assert await authenticate(async_session, " IVAN@example.com", PASSWORD) is user


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("ivan@example.com", "wrong password"),
        ("nobody@example.com", PASSWORD),
        ("ivan@example.com", "x" * 1000),
    ],
)
async def test_authenticate_returns_none_otherwise(
    async_session: AsyncSession, password_hash: str, email: str, password: str
) -> None:
    await add_user(async_session, password_hash, "ivan@example.com")

    assert await authenticate(async_session, email, password) is None


async def test_authenticate_replaces_an_outdated_hash(async_session: AsyncSession) -> None:
    old_hash = PasswordHasher(time_cost=2).hash(PASSWORD)
    user = await add_user(async_session, old_hash)

    await authenticate(async_session, user.email, PASSWORD)

    await async_session.refresh(user)
    assert user.password_hash != old_hash
    assert verify_password(PASSWORD, user.password_hash) == (True, None)
