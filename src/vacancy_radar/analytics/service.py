"""Read the analytics views for the `/analytics` page.

Everything here reads the materialized views of migration 0017, never the
vacancies themselves, so a page view costs a few index lookups however much
data there is. The numbers are as fresh as the last `analytics.refresh`;
`Summary.refreshed_at` says when that was. The rules for showing them (the
minimum sample, incomplete periods) are `domain/analytics.py`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.analytics.views import jobs_weekly, salary_weekly, skill_monthly, summary
from vacancy_radar.domain.analytics import (
    Quartiles,
    SalaryWeek,
    month_is_partial,
    salary_week,
    share,
    week_is_partial,
)
from vacancy_radar.domain.skills import SKILLS_BY_SLUG

TOP_SKILLS: Final = 15
# The salary series of every level, and of all of them together.
ALL_LEVELS: Final = "all"


@dataclass(frozen=True, slots=True)
class Summary:
    """What the views cover, and when they were refreshed."""

    refreshed_at: datetime
    jobs: int
    postings: int
    # Jobs that name a skill from the dictionary; skills and salaries count only these.
    with_skills: int
    # Of those, jobs with a monthly salary in US dollars.
    with_salary: int
    first_day: date
    last_day: date


@dataclass(frozen=True, slots=True)
class SkillCount:
    slug: str
    name: str
    n: int
    # Percentage of the month's jobs that name a skill.
    share: float


@dataclass(frozen=True, slots=True)
class TopSkills:
    month: date
    # Jobs that name a skill in the month: the base of every share.
    jobs: int
    partial: bool
    skills: tuple[SkillCount, ...]


@dataclass(frozen=True, slots=True)
class JobsWeek:
    week: date
    jobs: int
    with_skills: int
    partial: bool


async def read_summary(session: AsyncSession) -> Summary | None:
    """The summary, or None before any job has been counted."""
    row = (await session.execute(select(summary))).one_or_none()
    if row is None or not row.jobs:
        return None
    return Summary(
        refreshed_at=row.refreshed_at,
        jobs=row.jobs,
        postings=row.postings,
        with_skills=row.with_skills,
        with_salary=row.with_salary,
        first_day=row.first_day,
        last_day=row.last_day,
    )


async def months(session: AsyncSession) -> list[date]:
    """The months that have skill counts, newest first."""
    rows = await session.scalars(
        select(skill_monthly.c.month).distinct().order_by(skill_monthly.c.month.desc())
    )
    return list(rows)


async def top_skills(
    session: AsyncSession, month: date, *, first_day: date, today: date, limit: int = TOP_SKILLS
) -> TopSkills | None:
    """The most named skills of `month`, most named first; None for a month without data."""
    rows = (
        await session.execute(
            select(skill_monthly)
            .where(skill_monthly.c.month == month)
            .order_by(skill_monthly.c.n.desc(), skill_monthly.c.skill)
            .limit(limit)
        )
    ).all()
    if not rows:
        return None
    jobs = rows[0].jobs
    return TopSkills(
        month=month,
        jobs=jobs,
        partial=month_is_partial(month, first_day=first_day, today=today),
        skills=tuple(
            SkillCount(
                slug=row.skill,
                # A skill removed from the dictionary keeps its slug as its name.
                name=SKILLS_BY_SLUG[row.skill].name if row.skill in SKILLS_BY_SLUG else row.skill,
                n=row.n,
                share=share(row.n, jobs),
            )
            for row in rows
        ),
    )


async def salaries_by_week(
    session: AsyncSession, level: str, *, first_day: date, today: date
) -> list[SalaryWeek]:
    """The weekly salary quartiles of `level` (or `ALL_LEVELS`), oldest week first."""
    rows = await session.execute(
        select(salary_weekly).where(salary_weekly.c.level == level).order_by(salary_weekly.c.week)
    )
    return [
        salary_week(
            row.week,
            row.n,
            Quartiles(row.p25, row.median, row.p75),
            first_day=first_day,
            today=today,
        )
        for row in rows
    ]


async def jobs_by_week(
    session: AsyncSession, *, first_day: date, today: date
) -> dict[str, list[JobsWeek]]:
    """New jobs per week, by source slug and for `"all"` sources, oldest week first."""
    rows = await session.execute(
        select(jobs_weekly).order_by(jobs_weekly.c.source, jobs_weekly.c.week)
    )
    series: dict[str, list[JobsWeek]] = {}
    for row in rows:
        series.setdefault(row.source, []).append(
            JobsWeek(
                week=row.week,
                jobs=row.jobs,
                with_skills=row.with_skills,
                partial=week_is_partial(row.week, first_day=first_day, today=today),
            )
        )
    return series
