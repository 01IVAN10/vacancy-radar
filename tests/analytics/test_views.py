"""The analytics views of migration 0017, refreshed inside the test's transaction."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from tests.factories import add_clustered_vacancy, add_vacancy
from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.analytics.views import jobs_weekly, salary_weekly, skill_monthly, summary
from vacancy_radar.db.models import Vacancy

# Thursday 12 November 2026, 12:00 in Kyiv (UTC+2).
THURSDAY = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
WEEK = date(2026, 11, 9)
MONTH = date(2026, 11, 1)


def job(session: Session, **fields: Any) -> Vacancy:
    return add_clustered_vacancy(session, **({"first_seen_at": THURSDAY} | fields))


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


def test_skills_are_counted_once_per_job_among_jobs_with_skills(db_session: Session) -> None:
    first = job(db_session, skills=["python", "sql"])
    add_vacancy(db_session, cluster_id=first.cluster_id, skills=["python"], first_seen_at=THURSDAY)
    job(db_session, skills=["python"])
    job(db_session, skills=[])  # names no skill: not in the base

    refresh_views(db_session)

    assert rows(db_session, skill_monthly) == [(MONTH, "python", 2, 2), (MONTH, "sql", 1, 2)]


def test_salary_quartiles_by_week_and_level(db_session: Session) -> None:
    job(db_session, seniority=["senior"], **paid(4000, 6000))  # midpoint 5000
    job(db_session, seniority=["middle", "senior"], **paid(3000, None))  # a lower bound alone
    job(db_session, seniority=[], **paid(None, 1000))  # an upper bound alone
    job(db_session, skills=[], **paid(9000, 9000))  # names no skill: left out
    job(db_session, seniority=["senior"])  # no salary: left out

    refresh_views(db_session)

    assert rows(db_session, salary_weekly) == [
        (WEEK, "all", 3, 2000, 3000, 4000),
        (WEEK, "middle", 1, 3000, 3000, 3000),
        (WEEK, "senior", 2, 3500, 4000, 4500),
    ]


def test_the_shown_vacancy_carries_the_salary(db_session: Session) -> None:
    first = job(db_session, company=None)
    add_vacancy(db_session, cluster_id=first.cluster_id, first_seen_at=THURSDAY, **paid(2000, 2000))

    refresh_views(db_session)

    assert rows(db_session, salary_weekly) == [(WEEK, "all", 1, 2000, 2000, 2000)]


def test_weeks_are_kyiv_weeks(db_session: Session) -> None:
    # Sunday 22:30 UTC is already Monday in Kyiv.
    job(db_session, first_seen_at=datetime(2026, 11, 8, 22, 30, tzinfo=UTC))
    job(db_session, first_seen_at=datetime(2026, 11, 8, 21, 30, tzinfo=UTC))

    refresh_views(db_session)

    assert rows(db_session, jobs_weekly) == [
        (date(2026, 11, 2), "all", 1, 1),
        (date(2026, 11, 2), "djinni", 1, 1),
        (date(2026, 11, 9), "all", 1, 1),
        (date(2026, 11, 9), "djinni", 1, 1),
    ]


def test_a_job_on_two_sources_counts_once_in_all(db_session: Session) -> None:
    first = job(db_session, source="djinni")
    add_vacancy(db_session, cluster_id=first.cluster_id, source="dou", first_seen_at=THURSDAY)
    job(db_session, source="dou", skills=[])
    add_vacancy(db_session, first_seen_at=THURSDAY)  # no cluster yet: not counted

    refresh_views(db_session)

    assert rows(db_session, jobs_weekly) == [
        (WEEK, "all", 2, 1),
        (WEEK, "djinni", 1, 1),
        (WEEK, "dou", 2, 1),
    ]


def test_summary(db_session: Session) -> None:
    first = job(db_session, **paid(3000, 3000))
    add_vacancy(db_session, cluster_id=first.cluster_id, first_seen_at=THURSDAY)
    job(db_session, skills=[], first_seen_at=datetime(2026, 11, 5, 12, 0, tzinfo=UTC))

    jobs = refresh_views(db_session)

    row = db_session.execute(select(summary)).one()
    assert jobs == 2
    assert (row.jobs, row.postings, row.with_skills, row.with_salary) == (2, 3, 1, 1)
    assert (row.first_day, row.last_day) == (date(2026, 11, 5), date(2026, 11, 12))


def test_a_second_refresh_changes_nothing(db_session: Session) -> None:
    job(db_session, seniority=["senior"], **paid(3000, 3000))
    refresh_views(db_session)
    before = [rows(db_session, view) for view in (skill_monthly, salary_weekly, jobs_weekly)]

    refresh_views(db_session)

    assert [
        rows(db_session, view) for view in (skill_monthly, salary_weekly, jobs_weekly)
    ] == before


def test_empty_views_count_nothing(db_session: Session) -> None:
    assert refresh_views(db_session) == 0
    assert rows(db_session, jobs_weekly) == []
