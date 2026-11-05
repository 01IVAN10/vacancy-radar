import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

# Modules such as `vacancy_radar.worker` read settings at import time, so the
# test environment must be complete before any of them is imported.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://vacancy_radar:vacancy_radar@localhost:5432/vacancy_radar"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

DEFAULT_TEST_DATABASE_URL = (
    "postgresql://vacancy_radar:vacancy_radar@localhost:5432/vacancy_radar_test"
)


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only (the app does not support trio)."""
    return "asyncio"


def to_libpq(url: str) -> str:
    """Turn a SQLAlchemy-style URL (`postgresql+psycopg://`) into one libpq accepts."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


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
