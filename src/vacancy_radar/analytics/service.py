"""Read the analytics views for the `/analytics` page.

`read_analytics` is the one entry point: it reads the summary, decides from
it and the current moment which periods are incomplete (`domain.analytics.
Coverage`), and reads every series. It reads only the materialized views of
migration 0017, never the vacancies, so a page view costs a few lookups
however much data there is. The numbers are as fresh as the last
`analytics.refresh`; `Summary.refreshed_at` says when that was.

The views mark totals with the word `ALL` in the column of levels and
sources. That word stops here: what this module returns keeps totals apart
(`jobs_total`, `level=None`), so a chart can never draw the total as one more
source and count a job on two sites three times.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.analytics.views import jobs_weekly, salary_weekly, skill_monthly, summary
from vacancy_radar.db.models import Source
from vacancy_radar.domain.analytics import ALL, Coverage, Quartiles, SalaryWeek, salary_week, share
from vacancy_radar.domain.seniority import Level
from vacancy_radar.domain.skills import SKILLS_BY_SLUG

TOP_SKILLS: Final = 15


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
    # Late postings may still change the skills (`Coverage.month_is_settling`).
    settling: bool
    skills: tuple[SkillCount, ...]


@dataclass(frozen=True, slots=True)
class JobsWeek:
    week: date
    # Final once the week is over.
    jobs: int
    # Late postings may still raise it while the week is settling.
    with_skills: int
    partial: bool
    settling: bool


@dataclass(frozen=True, slots=True)
class SourceSite:
    """A site the data come from, to name and link on the page."""

    name: str
    url: str


@dataclass(frozen=True, slots=True)
class Analytics:
    summary: Summary
    # Months with skill counts, newest first.
    months: tuple[date, ...]
    # The month asked for, or the newest; None for a month without data.
    top_skills: TopSkills | None
    # Weekly salaries of one level, or of every level when None, oldest week first.
    level: Level | None
    salaries: tuple[SalaryWeek, ...]
    # New jobs per week, oldest week first: in all, each job once...
    jobs_total: tuple[JobsWeek, ...]
    # ...and by source slug, a job on two sources in both.
    jobs_by_source: dict[str, tuple[JobsWeek, ...]]
    # Every source by its slug, in slug order: the sites to credit.
    sources: dict[str, SourceSite]


async def read_analytics(
    session: AsyncSession, now: datetime, *, month: date | None = None, level: Level | None = None
) -> Analytics | None:
    """Everything the page shows at the moment `now`, or None before any job is counted.

    `month` picks the month of top skills (the newest by default), `level`
    the salary series (every level by default).
    """
    totals = await _read_summary(session)
    if totals is None:
        return None
    coverage = Coverage.at(totals.first_day, now)
    available = await _months(session)
    chosen = month if month is not None else available[0] if available else None
    jobs = await _jobs(session, coverage)
    return Analytics(
        summary=totals,
        months=available,
        top_skills=await _top_skills(session, chosen, coverage) if chosen else None,
        level=level,
        salaries=await _salaries(session, level or ALL, coverage),
        jobs_total=jobs.pop(ALL, ()),
        jobs_by_source=jobs,
        sources=await _sources(session),
    )


async def _read_summary(session: AsyncSession) -> Summary | None:
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


async def _months(session: AsyncSession) -> tuple[date, ...]:
    rows = await session.scalars(
        select(skill_monthly.c.month).distinct().order_by(skill_monthly.c.month.desc())
    )
    return tuple(rows)


async def _top_skills(session: AsyncSession, month: date, coverage: Coverage) -> TopSkills | None:
    rows = (
        await session.execute(
            select(skill_monthly)
            .where(skill_monthly.c.month == month)
            .order_by(skill_monthly.c.n.desc(), skill_monthly.c.skill)
            .limit(TOP_SKILLS)
        )
    ).all()
    if not rows:
        return None
    jobs = rows[0].jobs
    return TopSkills(
        month=month,
        jobs=jobs,
        partial=coverage.month_is_partial(month),
        settling=coverage.month_is_settling(month),
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


async def _salaries(
    session: AsyncSession, level: str, coverage: Coverage
) -> tuple[SalaryWeek, ...]:
    rows = await session.execute(
        select(salary_weekly).where(salary_weekly.c.level == level).order_by(salary_weekly.c.week)
    )
    return tuple(
        salary_week(row.week, row.n, Quartiles(row.p25, row.median, row.p75), coverage)
        for row in rows
    )


async def _jobs(session: AsyncSession, coverage: Coverage) -> dict[str, tuple[JobsWeek, ...]]:
    """Every series of `analytics_jobs_weekly`, keyed by its `source` column (`ALL` included)."""
    rows = await session.execute(
        select(jobs_weekly).order_by(jobs_weekly.c.source, jobs_weekly.c.week)
    )
    series: dict[str, list[JobsWeek]] = {}
    for row in rows:
        series.setdefault(row.source, []).append(
            JobsWeek(
                row.week,
                row.jobs,
                row.with_skills,
                partial=coverage.week_is_partial(row.week),
                settling=coverage.week_is_settling(row.week),
            )
        )
    return {source: tuple(weeks) for source, weeks in series.items()}


async def _sources(session: AsyncSession) -> dict[str, SourceSite]:
    rows = await session.execute(
        select(Source.slug, Source.name, Source.base_url).order_by(Source.slug)
    )
    return {row.slug: SourceSite(row.name, row.base_url) for row in rows}
