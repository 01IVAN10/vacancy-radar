"""The analytics views of migration 0017, refreshed inside the test's transaction."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, Table, inspect, select, text
from sqlalchemy.orm import Session

from tests.factories import add_clustered_vacancy, add_vacancy
from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.analytics.views import (
    MATERIALIZED_VIEWS,
    job,
    jobs_weekly,
    salary_weekly,
    skill_monthly,
    summary,
)
from vacancy_radar.db.models import Vacancy
from vacancy_radar.domain.analytics import ALL

# Thursday 12 November 2026, 12:00 in Kyiv (UTC+2).
THURSDAY = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
WEEK = date(2026, 11, 9)
MONTH = date(2026, 11, 1)


def first(session: Session, **fields: Any) -> Vacancy:
    """The first posting of a new job."""
    return add_clustered_vacancy(session, **({"first_seen_at": THURSDAY} | fields))


def another(session: Session, of: Vacancy, **fields: Any) -> Vacancy:
    """Another posting of the job of `of`."""
    return add_vacancy(session, cluster_id=of.cluster_id, **({"first_seen_at": THURSDAY} | fields))


def paid(usd_min: int | None, usd_max: int | None, **fields: Any) -> dict[str, Any]:
    return {
        "salary_min": usd_min,
        "salary_max": usd_max,
        "salary_currency": "USD",
        "salary_period": "month",
        "salary_usd_min": usd_min,
        "salary_usd_max": usd_max,
    } | fields


def rows(session: Session, view: Table) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in session.execute(select(view).order_by(*view.c))]


def test_a_job_has_the_skills_and_levels_any_of_its_postings_names(db_session: Session) -> None:
    djinni = first(db_session, skills=["python", "sql"], seniority=["middle"])
    another(db_session, djinni, source="dou", skills=["python", "go"], seniority=["senior"])

    refresh_views(db_session)

    [row] = db_session.execute(select(job)).all()
    assert (row.postings, row.skills, row.seniority) == (
        2,
        ["go", "python", "sql"],
        ["middle", "senior"],
    )
    assert rows(db_session, skill_monthly) == [
        (MONTH, "go", 1, 1),
        (MONTH, "python", 1, 1),
        (MONTH, "sql", 1, 1),
    ]


def test_skills_are_counted_among_jobs_that_name_one(db_session: Session) -> None:
    first(db_session, skills=["python", "sql"])
    first(db_session, skills=["python"])
    first(db_session, skills=[])  # names no skill: not in the base

    refresh_views(db_session)

    assert rows(db_session, skill_monthly) == [(MONTH, "python", 2, 2), (MONTH, "sql", 1, 2)]


def test_salary_quartiles_by_week_and_level(db_session: Session) -> None:
    first(db_session, seniority=["senior"], **paid(4000, 6000))  # midpoint 5000
    first(db_session, seniority=["middle", "senior"], **paid(3000, None))  # a lower bound alone
    first(db_session, seniority=[], **paid(None, 1000))  # an upper bound alone
    first(db_session, skills=[], **paid(9000, 9000))  # names no skill: left out
    first(db_session, seniority=["senior"])  # no salary: left out

    refresh_views(db_session)

    assert rows(db_session, salary_weekly) == [
        (WEEK, ALL, 3, 2000, 3000, 4000),
        (WEEK, "middle", 1, 3000, 3000, 3000),
        (WEEK, "senior", 2, 3500, 4000, 4500),
    ]


def test_the_salary_is_the_first_dollar_salary_of_the_job(db_session: Session) -> None:
    # An hourly rate in hryvnias has no monthly dollar value, so the DOU posting's salary
    # counts. The DOU posting names no skill, but the job does through the first posting.
    hourly = first(
        db_session,
        salary_min=500,
        salary_currency="UAH",
        salary_period="hour",
        seniority=["middle"],
        skills=["python"],
    )
    another(db_session, hourly, source="dou", skills=[], **paid(3000, 3000))
    another(db_session, hourly, **paid(9000, 9000))

    refresh_views(db_session)

    assert rows(db_session, salary_weekly) == [
        (WEEK, ALL, 1, 3000, 3000, 3000),
        (WEEK, "middle", 1, 3000, 3000, 3000),
    ]


def test_weeks_are_kyiv_weeks(db_session: Session) -> None:
    # Sunday 22:30 UTC is already Monday in Kyiv.
    first(db_session, first_seen_at=datetime(2026, 11, 8, 22, 30, tzinfo=UTC))
    first(db_session, first_seen_at=datetime(2026, 11, 8, 21, 30, tzinfo=UTC))

    refresh_views(db_session)

    assert rows(db_session, jobs_weekly) == [
        (date(2026, 11, 2), ALL, 1, 1),
        (date(2026, 11, 2), "djinni", 1, 1),
        (date(2026, 11, 9), ALL, 1, 1),
        (date(2026, 11, 9), "djinni", 1, 1),
    ]


def test_a_source_counts_a_job_in_the_week_it_appeared_there(db_session: Session) -> None:
    djinni = first(db_session, first_seen_at=datetime(2026, 11, 2, 10, 0, tzinfo=UTC))
    # The same job, posted on DOU ten days later, joins the cluster.
    another(db_session, djinni, source="dou")
    first(db_session, source="dou", skills=[])
    add_vacancy(db_session, first_seen_at=THURSDAY)  # no cluster yet: not counted

    refresh_views(db_session)

    assert rows(db_session, jobs_weekly) == [
        (date(2026, 11, 2), ALL, 1, 1),
        (date(2026, 11, 2), "djinni", 1, 1),
        (WEEK, ALL, 1, 0),
        (WEEK, "dou", 2, 1),
    ]


def test_summary(db_session: Session) -> None:
    paid_job = first(db_session, **paid(3000, 3000))
    another(db_session, paid_job)
    first(db_session, skills=[], first_seen_at=datetime(2026, 11, 5, 12, 0, tzinfo=UTC))

    jobs = refresh_views(db_session)

    row = db_session.execute(select(summary)).one()
    assert jobs == 2
    assert (row.jobs, row.postings, row.with_skills, row.with_salary) == (2, 3, 1, 1)
    assert (row.first_day, row.last_day) == (date(2026, 11, 5), date(2026, 11, 12))


def test_a_second_refresh_changes_nothing(db_session: Session) -> None:
    first(db_session, seniority=["senior"], **paid(3000, 3000))
    refresh_views(db_session)
    before = [rows(db_session, view) for view in MATERIALIZED_VIEWS if view is not summary]

    refresh_views(db_session)

    assert [rows(db_session, view) for view in MATERIALIZED_VIEWS if view is not summary] == before


def test_empty_views_count_nothing(db_session: Session) -> None:
    assert refresh_views(db_session) == 0
    assert rows(db_session, jobs_weekly) == []


def test_every_analytics_view_is_refreshed_with_the_columns_the_code_reads(
    migrated_engine: Engine,
) -> None:
    # A view added by a migration but missing from MATERIALIZED_VIEWS would never be
    # refreshed, and the page would show the numbers of the day it was created.
    with migrated_engine.connect() as connection:
        in_database = set(
            connection.scalars(
                text(
                    "SELECT matviewname FROM pg_matviews "
                    "WHERE schemaname = current_schema() AND matviewname LIKE 'analytics%'"
                )
            )
        )
    inspector = inspect(migrated_engine)

    assert in_database == {view.name for view in MATERIALIZED_VIEWS}
    for view in MATERIALIZED_VIEWS:
        columns = [column["name"] for column in inspector.get_columns(view.name)]
        assert columns == [column.name for column in view.c], view.name
