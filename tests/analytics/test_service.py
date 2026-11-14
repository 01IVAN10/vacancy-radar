from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import add_clustered_vacancy
from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.analytics.service import (
    ALL_LEVELS,
    JobsWeek,
    SkillCount,
    jobs_by_week,
    months,
    read_summary,
    salaries_by_week,
    top_skills,
)
from vacancy_radar.domain.analytics import MIN_SAMPLE, Quartiles

pytestmark = pytest.mark.anyio

FIRST_DAY = date(2026, 11, 5)
TODAY = date(2026, 11, 20)
THURSDAY = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


async def jobs(session: AsyncSession, count: int, **fields: Any) -> None:
    def add(sync: Any) -> None:
        for _ in range(count):
            add_clustered_vacancy(sync, **({"first_seen_at": THURSDAY} | fields))

    await session.run_sync(add)


async def refresh(session: AsyncSession) -> None:
    await session.run_sync(refresh_views)


async def test_nothing_before_the_first_refresh_with_jobs(async_session: AsyncSession) -> None:
    await refresh(async_session)

    assert await read_summary(async_session) is None
    assert await months(async_session) == []


async def test_summary(async_session: AsyncSession) -> None:
    await jobs(async_session, 2)
    await refresh(async_session)

    summary = await read_summary(async_session)

    assert summary is not None
    assert (summary.jobs, summary.postings, summary.with_skills, summary.with_salary) == (
        2,
        2,
        2,
        0,
    )
    assert summary.first_day == summary.last_day == date(2026, 11, 12)


async def test_top_skills_with_names_and_shares(async_session: AsyncSession) -> None:
    await jobs(async_session, 3, skills=["python", "sql"])
    await jobs(async_session, 1, skills=["removed-skill"])
    await refresh(async_session)

    top = await top_skills(async_session, date(2026, 11, 1), first_day=FIRST_DAY, today=TODAY)

    assert top is not None
    assert (top.jobs, top.partial) == (4, True)
    assert top.skills == (
        SkillCount("python", "Python", 3, 75.0),
        SkillCount("sql", "SQL", 3, 75.0),
        SkillCount("removed-skill", "removed-skill", 1, 25.0),
    )
    limited = await top_skills(
        async_session, date(2026, 11, 1), first_day=FIRST_DAY, today=TODAY, limit=1
    )
    assert limited is not None
    assert [skill.slug for skill in limited.skills] == ["python"]
    assert await months(async_session) == [date(2026, 11, 1)]
    assert (
        await top_skills(async_session, date(2026, 10, 1), first_day=FIRST_DAY, today=TODAY) is None
    )


async def test_salary_medians_below_the_minimum_sample_are_hidden(
    async_session: AsyncSession,
) -> None:
    salary = {"salary_currency": "USD", "salary_period": "month"}
    await jobs(
        async_session,
        MIN_SAMPLE,
        seniority=["middle"],
        salary_min=2000,
        salary_usd_min=2000,
        **salary,
    )
    await jobs(
        async_session, 1, seniority=["senior"], salary_min=5000, salary_usd_min=5000, **salary
    )
    await jobs(
        async_session,
        1,
        first_seen_at=THURSDAY - timedelta(days=7),
        salary_min=1000,
        salary_usd_min=1000,
        **salary,
    )
    await refresh(async_session)

    all_levels = await salaries_by_week(async_session, ALL_LEVELS, first_day=FIRST_DAY, today=TODAY)
    senior = await salaries_by_week(async_session, "senior", first_day=FIRST_DAY, today=TODAY)

    assert [(week.week, week.n, week.quartiles, week.partial) for week in all_levels] == [
        (date(2026, 11, 2), 1, None, True),
        (date(2026, 11, 9), MIN_SAMPLE + 1, Quartiles(2000, 2000, 2000), False),
    ]
    assert [(week.n, week.quartiles) for week in senior] == [(1, None)]


async def test_jobs_by_week_and_source(async_session: AsyncSession) -> None:
    await jobs(async_session, 2, source="dou")
    await jobs(async_session, 1, source="djinni", skills=[])
    await refresh(async_session)

    series = await jobs_by_week(async_session, first_day=FIRST_DAY, today=TODAY)

    week = date(2026, 11, 9)
    assert series == {
        "all": [JobsWeek(week, 3, 2, partial=False)],
        "djinni": [JobsWeek(week, 1, 0, partial=False)],
        "dou": [JobsWeek(week, 2, 2, partial=False)],
    }
