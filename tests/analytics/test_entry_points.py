"""The CLI and the Celery task, which commit for real against the test database."""

from datetime import UTC, datetime

import structlog
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.factories import add_clustered_vacancy
from tests.support import empty_database
from vacancy_radar.analytics.__main__ import main
from vacancy_radar.analytics.tasks import refresh_task
from vacancy_radar.analytics.views import MATERIALIZED_VIEWS, summary


def add_jobs(engine: Engine, count: int) -> None:
    with Session(engine) as session:
        for _ in range(count):
            add_clustered_vacancy(session, first_seen_at=datetime(2026, 11, 12, tzinfo=UTC))
        session.commit()


def counted_jobs(engine: Engine) -> int | None:
    with engine.connect() as connection:
        return connection.scalar(select(summary.c.jobs))


def test_cli_refreshes_the_views(committed_database: Engine) -> None:
    add_jobs(committed_database, 2)
    try:
        assert main([]) == 0
    finally:
        structlog.reset_defaults()

    assert counted_jobs(committed_database) == 2


def test_task_refreshes_the_views(committed_database: Engine) -> None:
    add_jobs(committed_database, 3)

    result = refresh_task.apply()

    assert result.successful(), result.result
    assert result.result == {"jobs": 3}
    assert counted_jobs(committed_database) == 3


def test_emptying_the_database_empties_the_views(committed_database: Engine) -> None:
    add_jobs(committed_database, 2)
    refresh_task.apply()

    empty_database(committed_database)

    with committed_database.connect() as connection:
        counts = [
            connection.scalar(select(summary.c.jobs))
            if view is summary
            else len(connection.execute(select(view)).all())
            for view in MATERIALIZED_VIEWS
        ]
    assert counts == [0] * len(MATERIALIZED_VIEWS)
