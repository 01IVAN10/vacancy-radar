"""Whether a normalized vacancy matches a subscription's filter. Pure: no I/O.

Every criterion the filter sets must hold, and a criterion it leaves empty
holds for any vacancy. What the vacancy does not state is read per field:

- **Skills** are the point of a skills filter, so a vacancy where none were
  found does not match one.
- **Seniority and work format** are found in about a third and a half of
  vacancies respectively (local data, 2026-11-07). A vacancy that names
  neither is unknown, not wrong, so it matches: otherwise a subscription to
  senior roles would never see two thirds of the market.
- **Salary**: a vacancy matches a minimum when the top of its range reaches
  it, since "$2,000–4,000" can pay $3,000. Without a monthly dollar figure
  (no salary stated, an hourly rate, or one not converted yet) the
  subscription's `include_unsalaried` decides.

See `docs/subscriptions.md` for the reasoning and examples.
"""

from collections.abc import Collection
from dataclasses import dataclass

from vacancy_radar.domain.seniority import Level
from vacancy_radar.domain.subscriptions import SalaryFloor, SubscriptionFilter
from vacancy_radar.domain.work_format import WorkFormat


@dataclass(frozen=True, slots=True)
class UsdRange:
    """A monthly salary in whole US dollars with at least one bound."""

    min_usd: int | None
    max_usd: int | None

    def __post_init__(self) -> None:
        if self.min_usd is None and self.max_usd is None:
            raise ValueError("a salary range needs at least one bound")
        if self.min_usd is not None and self.max_usd is not None and self.min_usd > self.max_usd:
            raise ValueError("a salary range cannot start above its end")

    @property
    def top(self) -> int:
        """The most the vacancy may pay: its upper bound, or its lower one if that is all."""
        return max(bound for bound in (self.min_usd, self.max_usd) if bound is not None)


@dataclass(frozen=True, slots=True)
class VacancyFacts:
    """What matching reads from a normalized vacancy."""

    skills: frozenset[str]
    seniority: tuple[Level, ...]
    work_formats: tuple[WorkFormat, ...]
    # None when there is no monthly dollar figure to compare.
    salary_usd: UsdRange | None


def matches(criteria: SubscriptionFilter, vacancy: VacancyFacts) -> bool:
    """True when the vacancy meets every criterion the filter sets."""
    return (
        _skills_match(criteria, vacancy.skills)
        and _known_or_overlapping(criteria.seniority, vacancy.seniority)
        and _known_or_overlapping(criteria.work_formats, vacancy.work_formats)
        and _salary_matches(criteria.salary, vacancy.salary_usd)
    )


def _skills_match(criteria: SubscriptionFilter, skills: frozenset[str]) -> bool:
    if not criteria.skills:
        return True
    if criteria.skills_match == "all":
        return skills.issuperset(criteria.skills)
    return not skills.isdisjoint(criteria.skills)


def _known_or_overlapping(wanted: Collection[str], stated: Collection[str]) -> bool:
    """Any value if none is wanted, and an unstated value counts as unknown, not as a mismatch."""
    if not wanted or not stated:
        return True
    return not set(wanted).isdisjoint(stated)


def _salary_matches(floor: SalaryFloor | None, salary: UsdRange | None) -> bool:
    if floor is None:
        return True
    if salary is None:
        return floor.include_unsalaried
    return salary.top >= floor.min_usd
