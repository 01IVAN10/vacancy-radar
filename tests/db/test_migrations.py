from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from tests.support import alembic_config
from vacancy_radar.db.base import Base


def test_models_match_migrations(migrated_engine: Engine) -> None:
    # Same check as `alembic check`: a model change without a migration fails here.
    with migrated_engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert diff == []


def test_downgrade_and_upgrade_round_trip(migrated_engine: Engine, test_database_url: str) -> None:
    config = alembic_config(test_database_url)

    command.downgrade(config, "base")
    tables_after_downgrade = set(inspect(migrated_engine).get_table_names())
    # Leave the database migrated for the tests that run after this one.
    command.upgrade(config, "head")

    assert tables_after_downgrade <= {"alembic_version"}
    assert {"source", "raw_vacancy", "collect_run", "exchange_rate"} <= set(
        inspect(migrated_engine).get_table_names()
    )
