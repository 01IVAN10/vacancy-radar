"""Rules for showing market aggregates honestly. Pure: no I/O.

Every aggregate carries its sample size `n`, and a salary median of fewer
than `MIN_SAMPLE` jobs is not shown at all: a weekly median of four salaries
says more about those four jobs than about the market. On 2026-11-14, a
week had about 190 jobs with a dollar salary in all, but only 3 to 13 per
level, so per-level weeks fall on both sides of the line.

Periods are Kyiv calendar weeks (Monday first) and months of a job's first
sighting. The first period of the data (collection started on 2026-11-05, a
Thursday) and the current one are incomplete, and counts in them are lower
for that reason alone, so they are marked.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

MIN_SAMPLE: Final = 10
DAYS_PER_WEEK: Final = 7


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


def salary_week(
    week: date, n: int, quartiles: Quartiles, *, first_day: date, today: date
) -> SalaryWeek:
    """A week's salaries, with the quartiles hidden below `MIN_SAMPLE`."""
    return SalaryWeek(
        week=week,
        n=n,
        quartiles=quartiles if n >= MIN_SAMPLE else None,
        partial=week_is_partial(week, first_day=first_day, today=today),
    )


def week_is_partial(week: date, *, first_day: date, today: date) -> bool:
    """Whether the data cover only part of the week starting on Monday `week`.

    It is partial when collection started after its Monday, or when it has
    not ended yet.
    """
    return first_day > week or today < week + timedelta(days=DAYS_PER_WEEK - 1)


def month_is_partial(month: date, *, first_day: date, today: date) -> bool:
    """Whether the data cover only part of the month starting on `month`."""
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_day > month or today < next_month - timedelta(days=1)


def share(n: int, total: int) -> float:
    """`n` as a percentage of `total`, to one decimal; 0 when there is no total."""
    return round(100 * n / total, 1) if total else 0.0
