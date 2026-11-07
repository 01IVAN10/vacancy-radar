from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

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
    assert {
        "source",
        "raw_vacancy",
        "collect_run",
        "exchange_rate",
        "app_user",
        "subscription",
    } <= set(inspect(migrated_engine).get_table_names())


def test_downgrade_keeps_an_interrupted_run_as_a_failed_request(
    migrated_engine: Engine, test_database_url: str
) -> None:
    config = alembic_config(test_database_url)
    with migrated_engine.begin() as connection:
        source_id = connection.scalar(
            text(
                "INSERT INTO source (slug, name, base_url)"
                " VALUES ('fake', 'Fake', 'https://fake.example') RETURNING id"
            )
        )
        connection.execute(
            text(
                "INSERT INTO collect_run (source_id, started_at, status)"
                " VALUES (:source_id, now(), 'running')"
            ),
            {"source_id": source_id},
        )

    try:
        command.downgrade(config, "0005")
        with migrated_engine.connect() as connection:
            row = connection.execute(
                text("SELECT status, error_type, finished_at = started_at FROM collect_run")
            ).one()
    finally:
        command.upgrade(config, "head")
        with migrated_engine.begin() as connection:
            connection.execute(text("TRUNCATE collect_run, source RESTART IDENTITY CASCADE"))

    assert tuple(row) == ("failed", "Abandoned", True)
