"""Alembic environment: runs migrations against the configured database.

The URL comes from `sqlalchemy.url` when the caller sets it (the test suite
does), and otherwise from the application settings, so `alembic upgrade head`
uses the same `DATABASE_URL` as the app.
"""

from alembic import context
from sqlalchemy import pool

from vacancy_radar.config import get_settings
from vacancy_radar.db import models  # noqa: F401  (registers tables on Base.metadata)
from vacancy_radar.db.base import Base
from vacancy_radar.db.engine import make_engine

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or str(get_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout (`alembic upgrade head --sql`) without connecting."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations in one transaction."""
    engine = make_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
