import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import psycopg
import pytest
import redis
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy import Engine, NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from tests.support import (
    DEFAULT_TEST_DATABASE_URL,
    PASSWORD,
    TEST_BOT_USERNAME,
    TEST_SECRET_KEY,
    alembic_config,
    redis_test_url,
    to_libpq,
    to_sqlalchemy,
)
from vacancy_radar.api.deps import Sessions
from vacancy_radar.auth.passwords import hash_password
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import (
    get_engine,
    get_sessionmaker,
    make_async_engine,
    make_engine,
)

# Modules such as `vacancy_radar.worker` read settings at import time, so the
# test environment must be complete before any of them is imported. The
# default DATABASE_URL is the test database, so code that reads settings can
# never write to the development database by accident.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    to_sqlalchemy(to_libpq(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))),
)
os.environ.setdefault("REDIS_URL", redis_test_url())
os.environ.setdefault("SECRET_KEY", TEST_SECRET_KEY)
# A made-up bot, and no token: a developer's `.env` may hold a real bot, and
# no test may reach it.
os.environ.setdefault("TELEGRAM_BOT_USERNAME", TEST_BOT_USERNAME)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only (the app does not support trio)."""
    return "asyncio"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """URL of the test database, created on first use.

    Refuses any database whose name does not end in `_test`, so a misconfigured
    environment can never point the suite at real data.
    """
    url = to_libpq(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
    params = conninfo_to_dict(url)
    dbname = str(params.get("dbname", ""))
    if not dbname.endswith("_test"):
        pytest.exit(f"TEST_DATABASE_URL must name a database ending in '_test', got {dbname!r}")

    maintenance_url = make_conninfo(url, dbname="postgres")
    try:
        with psycopg.connect(maintenance_url, autocommit=True, connect_timeout=5) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
            ).fetchone()
            if exists is None:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    except psycopg.OperationalError as exc:
        # Fail only the tests that need the database; the rest of the suite still runs.
        pytest.fail(
            f"Cannot reach Postgres for tests ({exc}). "
            "Start it with `docker compose up -d postgres`."
        )
    return url


@pytest.fixture
def db_conn(test_database_url: str) -> Iterator[psycopg.Connection]:
    """Connection whose work is rolled back after each test, keeping tests isolated."""
    with psycopg.connect(test_database_url) as conn:
        try:
            yield conn
        finally:
            conn.rollback()


@pytest.fixture(scope="session")
def migrated_engine(test_database_url: str) -> Iterator[Engine]:
    """Sync engine on the test database, migrated to the latest revision.

    Tests run the real migrations instead of `metadata.create_all`, so a
    migration that drifts from the models fails the suite.
    """
    command.upgrade(alembic_config(test_database_url), "head")
    engine = make_engine(to_sqlalchemy(test_database_url))
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """Session whose work, commits included, is rolled back after each test.

    The outer transaction belongs to the fixture. `session.commit()` inside the
    code under test only releases a savepoint, so nothing reaches the database.
    """
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def committed_database(
    migrated_engine: Engine, test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Point the app's own sessions at the test database, for code that commits.

    The CLI and the Celery tasks open their sessions through settings and
    commit for real, so `db_session` cannot isolate them. Instead the tables
    are emptied after each test. Yields the engine to check rows with.
    """
    monkeypatch.setenv("DATABASE_URL", to_sqlalchemy(test_database_url))
    # Set to empty rather than removed: without the variable, settings would
    # fall back to a developer's `.env`, which may disable a source.
    monkeypatch.setenv("DISABLED_SOURCES", "")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield migrated_engine
    get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_settings.cache_clear()
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE notification, delivery, vacancy, vacancy_cluster, skill, "
                "exchange_rate, collect_run, raw_vacancy, source, subscription, "
                "telegram_link_token, app_user RESTART IDENTITY"
            )
        )


@pytest.fixture
async def async_session(
    migrated_engine: Engine, test_database_url: str
) -> AsyncIterator[AsyncSession]:
    """Async session whose work, commits included, is rolled back after each test.

    The same arrangement as `db_session`, on the async engine the web app uses.
    `NullPool`, because a pooled connection would outlive the test's event loop.
    """
    engine = make_async_engine(to_sqlalchemy(test_database_url), poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
def test_sessions(async_session: AsyncSession) -> "Sessions":
    """A session factory for the web app that hands out `async_session` every time."""

    @asynccontextmanager
    async def shared_session() -> AsyncIterator[AsyncSession]:
        yield async_session  # left open: the fixture rolls it back after the test

    return shared_session


@pytest.fixture
def app(test_sessions: "Sessions") -> FastAPI:
    """The web app on the test's session, so requests see the test's rows."""
    # Imported here: the module builds an app at import time, which needs the
    # environment set above.
    from vacancy_radar.api.app import create_app

    return create_app(sessions=test_sessions)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client for `app`. It keeps cookies between requests, as a browser does."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture(scope="session")
def password_hash() -> str:
    """The hash of `tests.support.PASSWORD`, computed once: Argon2 is slow on purpose."""
    return hash_password(PASSWORD)


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    """Client on the test Redis database, emptied before and after each test.

    Uses `TEST_REDIS_URL`, never `REDIS_URL`, so the suite cannot flush the
    database a running worker uses as its broker.
    """
    client = redis.Redis.from_url(redis_test_url())
    try:
        client.flushdb()
    except redis.ConnectionError as exc:
        client.close()
        pytest.fail(
            f"Cannot reach Redis for tests ({exc}). Start it with `docker compose up -d redis`."
        )
    try:
        yield client
    finally:
        client.flushdb()
        client.close()
