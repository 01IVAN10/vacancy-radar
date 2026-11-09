"""The clustering CLI against the test database: it commits, so tables are emptied after."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tests.notify.factories import JOB_TEXT, add_vacancy
from vacancy_radar.clusters import __main__ as clusters_cli
from vacancy_radar.clusters.__main__ import main
from vacancy_radar.db import locks
from vacancy_radar.db.models import Vacancy


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def events(stderr: str) -> dict[str, dict[str, Any]]:
    """The logged events by name."""
    parsed = [json.loads(line) for line in stderr.splitlines()]
    return {event["event"]: event for event in parsed}


def add_pair(engine: Engine) -> None:
    """One job posted on Djinni and on DOU."""
    with Session(engine) as session:
        add_vacancy(session, company=None, description=JOB_TEXT)
        add_vacancy(session, source="dou", company="Acme", description=JOB_TEXT)
        session.commit()


def test_assigns_clusters_and_logs_the_counts(
    committed_database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    add_pair(committed_database)

    assert main([]) == 0
    first = events(capsys.readouterr().err)["clusters.assigned"]
    assert main([]) == 0
    second = events(capsys.readouterr().err)["clusters.assigned"]

    assert (first["opened"], first["joined"]) == (1, 1)
    assert (second["opened"], second["joined"]) == (0, 0)


def test_works_off_a_backlog_in_several_runs(
    committed_database: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    add_pair(committed_database)
    monkeypatch.setattr(clusters_cli, "MAX_PER_RUN", 1)

    assert main([]) == 0

    assigned = events(capsys.readouterr().err)["clusters.assigned"]
    assert (assigned["opened"], assigned["joined"]) == (1, 1)
    with committed_database.connect() as connection:
        unclustered = select(func.count()).select_from(Vacancy).where(Vacancy.cluster_id.is_(None))
        assert connection.scalar(unclustered) == 0


def test_logs_when_another_run_is_clustering(
    committed_database: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    add_pair(committed_database)

    with committed_database.connect() as other:
        other.execute(select(func.pg_advisory_lock(locks.CLUSTER, 0)))
        try:
            assert main([]) == 0
        finally:
            other.execute(select(func.pg_advisory_unlock(locks.CLUSTER, 0)))

    assert "clusters.busy" in events(capsys.readouterr().err)
