"""Rules for showing market aggregates honestly. Pure: no I/O.

Every aggregate carries its sample size `n`, and a salary median of fewer
than `MIN_SAMPLE` jobs is not shown at all: a weekly median of four salaries
says more about those four jobs than about the market. On 2026-11-14, a
week had about 190 jobs with a dollar salary in all, but only a dozen or so
per level, so per-level weeks fall on both sides of the line.

Periods are Kyiv calendar weeks (Monday first) and months of a job's first
sighting. `Coverage` marks two things about a period:

- **partial**: the data miss part of it. The first period (collection
  started on 2026-11-05, a Thursday) and the current one are partial, and
  their counts are lower for that reason alone.
- **settling**: its numbers may still change. A job's other postings can
  join it up to `CANDIDATE_WINDOW` (30 days) after it first appeared, and
  bring skills, levels or a salary with them, so the skills, the salaries
  and the jobs that name a skill of a period settle only that long after
  it ends. Counts of jobs do not change once a period is over.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Self

from vacancy_radar.domain.dedup import CANDIDATE_WINDOW
from vacancy_radar.domain.normalize import kyiv_date

MIN_SAMPLE: Final = 10
# The series of every level together, and of every source together, as
# migration 0017 writes it into the views. Only `analytics/service.py` uses
# it; what it returns keeps totals apart from levels and sources.
ALL: Final = "all"

WEEK: Final = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Which Kyiv days the data cover: from the first sighting through today."""

    first_day: date
    today: date

    @classmethod
    def at(cls, first_day: date, now: datetime) -> Self:
        """The coverage at the moment `now`, an aware datetime; today is its Kyiv day."""
        return cls(first_day=first_day, today=kyiv_date(now))

    def week_is_partial(self, week: date) -> bool:
        """Whether the data miss part of the week starting on Monday `week`.

        They do when collection started after its Monday, or when its last
        day, Sunday, has not ended yet: on Sunday the week is still being
        collected.
        """
        return self.first_day > week or self.today < week + WEEK

    def month_is_partial(self, month: date) -> bool:
        """Whether the data miss part of the month starting on `month`."""
        return self.first_day > month or self.today < _next_month(month)

    def week_is_settling(self, week: date) -> bool:
        """Whether late postings may still change the week's skills and salaries."""
        return self.today < week + WEEK + CANDIDATE_WINDOW

    def month_is_settling(self, month: date) -> bool:
        """Whether late postings may still change the month's skills."""
        return self.today < _next_month(month) + CANDIDATE_WINDOW


def _next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


@dataclass(frozen=True, slots=True)
class Quartiles:
    """The 25th, 50th and 75th percentile of monthly salaries in US dollars."""

    p25: int
    median: int
    p75: int


@dataclass(frozen=True, slots=True)
class SalaryWeek:
    """Salaries of the jobs first seen in one week."""

    week: date
    n: int
    # None when `n` is below `MIN_SAMPLE`.
    quartiles: Quartiles | None
    partial: bool
    settling: bool


def salary_week(week: date, n: int, quartiles: Quartiles, coverage: Coverage) -> SalaryWeek:
    """A week's salaries, with the quartiles hidden below `MIN_SAMPLE`."""
    return SalaryWeek(
        week=week,
        n=n,
        quartiles=quartiles if n >= MIN_SAMPLE else None,
        partial=coverage.week_is_partial(week),
        settling=coverage.week_is_settling(week),
    )


def share(n: int, total: int) -> float:
    """`n` as a percentage of `total`, to one decimal; 0 when there is no total."""
    return round(100 * n / total, 1) if total else 0.0
