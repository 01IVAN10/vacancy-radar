import os
from collections.abc import Iterator

import psycopg
import pytest
import redis
from alembic import command
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.support import (
    DEFAULT_TEST_DATABASE_URL,
    alembic_config,
    redis_test_url,
    to_libpq,
    to_sqlalchemy,
)
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker, make_engine

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
    get_sessionmaker.cache_clear()
    yield migrated_engine
    get_sessionmaker().kw["bind"].dispose()
    get_sessionmaker.cache_clear()
    get_settings.cache_clear()
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE vacancy, skill, exchange_rate, collect_run, raw_vacancy, source "
                "RESTART IDENTITY"
            )
        )


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
