"""Helpers shared by tests and fixtures. Fixtures themselves live in `conftest.py`."""

import os
from pathlib import Path

from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.auth.csrf import CSRF_COOKIE
from vacancy_radar.auth.tokens import issue_token
from vacancy_radar.db.models import User

DEFAULT_TEST_DATABASE_URL = (
    "postgresql://vacancy_radar:vacancy_radar@localhost:5432/vacancy_radar_test"
)
# Long enough for `signing_key`, and obviously not a real key.
TEST_SECRET_KEY = "test-only-secret-key-not-used-anywhere-else"
# The password of every account `add_user` creates.
PASSWORD = "correct horse battery staple"
# Redis database 15, away from db 0 that the local Compose stack uses as its broker.
DEFAULT_TEST_REDIS_URL = "redis://localhost:6379/15"


def redis_test_url() -> str:
    """URL of the Redis database tests may flush: `TEST_REDIS_URL`, never `REDIS_URL`."""
    return os.environ.get("TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL)


ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def to_libpq(url: str) -> str:
    """Turn a SQLAlchemy-style URL (`postgresql+psycopg://`) into one libpq accepts."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def to_sqlalchemy(url: str) -> str:
    """Turn a libpq URL into one SQLAlchemy maps to the psycopg 3 driver."""
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def alembic_config(database_url: str) -> Config:
    """Alembic config for the project migrations, pointed at `database_url`."""
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", to_sqlalchemy(database_url))
    return config


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_bytes(relative_path: str) -> bytes:
    """Saved source response from `tests/fixtures/`, as the server sent it."""
    return (FIXTURES / relative_path).read_bytes()


async def add_user(
    session: AsyncSession, password_hash: str, email: str = "ivan@example.com"
) -> User:
    """An account whose password is `PASSWORD`, without paying for an Argon2 hash."""
    user = User(email=email, password_hash=password_hash)
    session.add(user)
    await session.flush()
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    """Give the client a session cookie for `user`, as a successful sign-in would."""
    # The cookie jar files cookies from a dotless host such as `test` under
    # `test.local`. Setting it there too lets the server's sign-out replace it.
    domain = (
        client.base_url.host if "." in client.base_url.host else f"{client.base_url.host}.local"
    )
    client.cookies.set("access_token", issue_token(user.id, key=TEST_SECRET_KEY), domain=domain)


async def csrf_token(client: AsyncClient) -> str:
    """The client's CSRF token, fetched with a page view the way a browser gets it."""
    if CSRF_COOKIE not in client.cookies:
        response = await client.get("/")
        response.raise_for_status()
    return client.cookies[CSRF_COOKIE]
