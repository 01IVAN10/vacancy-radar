"""Helpers shared by tests and fixtures. Fixtures themselves live in `conftest.py`."""

import os
from pathlib import Path

from alembic.config import Config

DEFAULT_TEST_DATABASE_URL = (
    "postgresql://vacancy_radar:vacancy_radar@localhost:5432/vacancy_radar_test"
)
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
