from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import add_clustered_vacancy, add_vacancy
from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.analytics.service import (
    Analytics,
    JobsWeek,
    SkillCount,
    SourceSite,
    read_analytics,
)
from vacancy_radar.domain.analytics import MIN_SAMPLE, Quartiles

pytestmark = pytest.mark.anyio

THURSDAY = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
# Friday 20 November in Kyiv: the week of 9 November is over, November is not.
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
NOVEMBER = date(2026, 11, 1)
SALARY: dict[str, Any] = {"salary_currency": "USD", "salary_period": "month"}


async def jobs(session: AsyncSession, count: int, **fields: Any) -> None:
    def add(sync: Any) -> None:
        for _ in range(count):
            add_clustered_vacancy(sync, **({"first_seen_at": THURSDAY} | fields))

    await session.run_sync(add)


async def analytics(session: AsyncSession, **choice: Any) -> Analytics:
    await session.run_sync(refresh_views)
    result = await read_analytics(session, NOW, **choice)
    assert result is not None
    return result


async def test_nothing_before_any_job_is_counted(async_session: AsyncSession) -> None:
    await async_session.run_sync(refresh_views)

    assert await read_analytics(async_session, NOW) is None


async def test_summary(async_session: AsyncSession) -> None:
    await jobs(async_session, 2)

    summary = (await analytics(async_session)).summary

    assert (summary.jobs, summary.postings, summary.with_skills, summary.with_salary) == (
        2,
        2,
        2,
        0,
    )
    assert summary.first_day == summary.last_day == date(2026, 11, 12)


async def test_top_skills_of_the_newest_month_with_names_and_shares(
    async_session: AsyncSession,
) -> None:
    await jobs(async_session, 3, skills=["python", "sql"])
    await jobs(async_session, 1, skills=["removed-skill"])
    await jobs(async_session, 1, skills=["go"], first_seen_at=THURSDAY - timedelta(days=30))

    result = await analytics(async_session)

    assert result.months == (NOVEMBER, date(2026, 10, 1))
    top = result.top_skills
    assert top is not None
    assert (top.month, top.jobs, top.partial, top.settling) == (NOVEMBER, 4, True, True)
    assert top.skills == (
        SkillCount("python", "Python", 3, 75.0),
        SkillCount("sql", "SQL", 3, 75.0),
        SkillCount("removed-skill", "removed-skill", 1, 25.0),
    )


async def test_top_skills_of_a_chosen_month(async_session: AsyncSession) -> None:
    await jobs(async_session, 1, skills=["go"], first_seen_at=THURSDAY - timedelta(days=30))
    await jobs(async_session, 1)

    october = await analytics(async_session, month=date(2026, 10, 1))
    empty = await read_analytics(async_session, NOW, month=date(2026, 9, 1))

    assert october.top_skills is not None
    assert [skill.slug for skill in october.top_skills.skills] == ["go"]
    assert empty is not None
    assert empty.top_skills is None


async def test_salary_medians_below_the_minimum_sample_are_hidden(
    async_session: AsyncSession,
) -> None:
    await jobs(
        async_session,
        MIN_SAMPLE,
        seniority=["middle"],
        salary_min=2000,
        salary_usd_min=2000,
        **SALARY,
    )
    await jobs(
        async_session, 1, seniority=["senior"], salary_min=5000, salary_usd_min=5000, **SALARY
    )
    await jobs(
        async_session,
        1,
        first_seen_at=THURSDAY - timedelta(days=7),
        salary_min=1000,
        salary_usd_min=1000,
        **SALARY,
    )

    every_level = await analytics(async_session)
    senior = await read_analytics(async_session, NOW, level="senior")

    assert every_level.level is None
    assert [(w.week, w.n, w.quartiles, w.partial) for w in every_level.salaries] == [
        (date(2026, 11, 2), 1, None, True),  # collection started on Thursday 5 November
        (date(2026, 11, 9), MIN_SAMPLE + 1, Quartiles(2000, 2000, 2000), False),
    ]
    assert senior is not None
    assert [(w.n, w.quartiles) for w in senior.salaries] == [(1, None)]


async def test_jobs_by_week_and_source(async_session: AsyncSession) -> None:
    # Monday 9 November in Kyiv: the data cover the whole week, and it is over by NOW.
    monday = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    await jobs(async_session, 2, source="dou", first_seen_at=monday)
    await jobs(async_session, 1, source="djinni", skills=[])

    week = date(2026, 11, 9)
    result = await analytics(async_session)

    # The total is kept apart, so a chart of the sources cannot draw it as a source.
    assert result.jobs_total == (JobsWeek(week, 3, 2, partial=False, settling=True),)
    assert result.jobs_by_source == {
        "djinni": (JobsWeek(week, 1, 0, partial=False, settling=True),),
        "dou": (JobsWeek(week, 2, 2, partial=False, settling=True),),
    }


async def test_names_every_source_with_its_site(async_session: AsyncSession) -> None:
    await jobs(async_session, 1, source="dou")
    await jobs(async_session, 1, source="djinni")

    result = await analytics(async_session)

    assert result.sources == {
        "djinni": SourceSite("Djinni", "https://djinni.co"),
        "dou": SourceSite("DOU", "https://jobs.dou.ua"),
    }


async def test_the_current_week_is_partial_on_its_sunday(async_session: AsyncSession) -> None:
    await jobs(async_session, 1)
    await async_session.run_sync(refresh_views)

    # Sunday 15 November, 12:00 in Kyiv: the week of 9 November is still being collected.
    sunday = await read_analytics(async_session, datetime(2026, 11, 15, 10, 0, tzinfo=UTC))

    assert sunday is not None
    assert sunday.jobs_total[0].partial


async def test_a_late_twin_changes_a_settling_week(async_session: AsyncSession) -> None:
    # The review's case: a Djinni posting without salary or level on Tuesday 3 November,
    # and its DOU twin with both on 17 November, after the week of 2 November is over.
    tuesday = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    djinni = await async_session.run_sync(
        lambda sync: add_clustered_vacancy(sync, first_seen_at=tuesday)
    )
    before = await analytics(async_session)
    await async_session.run_sync(
        lambda sync: add_vacancy(
            sync,
            cluster_id=djinni.cluster_id,
            source="dou",
            first_seen_at=datetime(2026, 11, 17, 10, 0, tzinfo=UTC),
            seniority=["senior"],
            salary_min=4000,
            salary_usd_min=4000,
            **SALARY,
        )
    )

    after = await analytics(async_session)

    assert before.salaries == ()
    [week] = after.salaries
    assert (week.week, week.n, week.settling) == (date(2026, 11, 2), 1, True)
    # Thirty days after the week ended, no twin can join it any more.
    later = await read_analytics(async_session, datetime(2026, 12, 9, 12, 0, tzinfo=UTC))
    assert later is not None
    assert not later.salaries[0].settling
