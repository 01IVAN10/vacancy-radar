"""Alembic environment: runs migrations against the configured database.

The URL comes from `sqlalchemy.url` when the caller sets it (the test suite
does), and otherwise from the application settings, so `alembic upgrade head`
uses the same `DATABASE_URL` as the app.

Run from the command line (the Compose `migrate` service), it logs each
applied revision as JSON through the project's structlog setup, so
`docker compose logs migrate` shows what changed. Callers that run
migrations from Python, like the test suite, keep their own logging.
"""

from collections.abc import Collection, Mapping
from typing import Any

import structlog
from alembic import context
from alembic.migration import MigrationContext, MigrationInfo
from alembic.script import Script
from sqlalchemy import pool

from vacancy_radar.config import get_settings
from vacancy_radar.db import models  # noqa: F401  (registers tables on Base.metadata)
from vacancy_radar.db.base import Base
from vacancy_radar.db.engine import make_engine
from vacancy_radar.log import configure_logging

config = context.config
target_metadata = Base.metadata
log = structlog.get_logger()

# `cmd_opts` is set only when Alembic runs from its command line.
if config.cmd_opts is not None:
    configure_logging(get_settings().log_level)


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


def log_step(
    ctx: MigrationContext,
    step: MigrationInfo,
    heads: Collection[Any],
    run_args: Mapping[str, Any],
) -> None:
    """Log one applied step; Alembic calls this after each one, by keyword."""
    direction = "stamp" if step.is_stamp else "upgrade" if step.is_upgrade else "downgrade"
    revision = step.up_revision
    log.info(
        "migrate.applied",
        direction=direction,
        revision=step.up_revision_id,
        down_revision=", ".join(step.down_revision_ids) or None,
        description=revision.doc if isinstance(revision, Script) else None,
    )


def run_migrations_online() -> None:
    """Connect to the database and apply migrations in one transaction."""
    engine = make_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, on_version_apply=log_step
        )
        with context.begin_transaction():
            context.run_migrations()
        log.info("migrate.finished", heads=sorted(context.get_context().get_current_heads()))
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
