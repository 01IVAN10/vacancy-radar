"""Whether a normalized vacancy matches a subscription's filter. Pure: no I/O.

Each criterion the filter sets gives a verdict on the vacancy: met, failed,
or unknown when the vacancy does not state what the criterion asks about.
A vacancy matches when no criterion failed and at least one was met. So an
unknown value never rules a vacancy out, but it cannot be the only reason
to send one either, and a filter of criteria that are rarely stated does
not match nearly every vacancy.

`docs/subscriptions.md` (Matching) lists what each criterion reads and why.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from typing import Self

from vacancy_radar.domain.choices import one_of
from vacancy_radar.domain.money import UsdRange
from vacancy_radar.domain.seniority import LEVELS, Level
from vacancy_radar.domain.subscriptions import SalaryFloor, SubscriptionFilter
from vacancy_radar.domain.work_format import WORK_FORMATS, WorkFormat


class Verdict(Enum):
    """What one criterion says about one vacancy."""

    MET = auto()
    UNKNOWN = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class VacancyFacts:
    """What matching reads from a normalized vacancy."""

    skills: frozenset[str]
    seniority: tuple[Level, ...]
    work_formats: tuple[WorkFormat, ...]
    # None when there is no monthly dollar figure to compare.
    salary_usd: UsdRange | None

    @classmethod
    def from_stored(
        cls,
        *,
        skills: Iterable[str],
        seniority: Sequence[str],
        work_formats: Sequence[str],
        salary_usd_min: int | None,
        salary_usd_max: int | None,
    ) -> Self:
        """Rebuild the facts from the values of a stored `vacancy` row.

        Raises `ValueError` for values the database constraints should have
        kept out: an unknown level or format, or a dollar range that starts
        above its end.
        """
        return cls(
            skills=frozenset(skills),
            seniority=tuple(one_of(level, LEVELS) for level in seniority),
            work_formats=tuple(one_of(fmt, WORK_FORMATS) for fmt in work_formats),
            salary_usd=UsdRange.from_stored(salary_usd_min, salary_usd_max),
        )


def matches(criteria: SubscriptionFilter, vacancy: VacancyFacts) -> bool:
    """True when no criterion the filter sets fails and at least one is met."""
    verdicts = [
        verdict
        for verdict in (
            _skills(criteria, vacancy.skills),
            _overlap(criteria.seniority, vacancy.seniority),
            _overlap(criteria.work_formats, vacancy.work_formats),
            _salary(criteria.salary, vacancy.salary_usd),
        )
        if verdict is not None
    ]
    return Verdict.FAILED not in verdicts and Verdict.MET in verdicts


# Each check returns None when the filter does not set its criterion.


def _skills(criteria: SubscriptionFilter, skills: frozenset[str]) -> Verdict | None:
    """Skills are what a skills filter is about, so a vacancy without any fails it."""
    if not criteria.skills:
        return None
    if criteria.skills_match == "all":
        met = skills.issuperset(criteria.skills)
    else:
        met = not skills.isdisjoint(criteria.skills)
    return Verdict.MET if met else Verdict.FAILED


def _overlap[T: str](wanted: tuple[T, ...], stated: tuple[T, ...]) -> Verdict | None:
    """Met by any wanted value; a vacancy that states none is unknown."""
    if not wanted:
        return None
    if not stated:
        return Verdict.UNKNOWN
    return Verdict.FAILED if set(wanted).isdisjoint(stated) else Verdict.MET


def _salary(floor: SalaryFloor | None, salary: UsdRange | None) -> Verdict | None:
    """Met when the top of the range reaches the minimum.

    Without a dollar figure the vacancy is unknown if the subscription
    includes vacancies without a salary, and fails otherwise.
    """
    if floor is None:
        return None
    if salary is None:
        return Verdict.UNKNOWN if floor.include_unsalaried else Verdict.FAILED
    return Verdict.MET if salary.top >= floor.min_usd else Verdict.FAILED
