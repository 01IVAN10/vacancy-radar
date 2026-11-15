"""What a subscription asks for, and the rules a new or edited one must follow. Pure: no I/O.

A subscription is a filter over normalized vacancies. An empty criterion
means "any": a subscription with only `seniority = ("senior",)` wants every
senior vacancy, whatever its skills. At least one criterion must be set,
because a subscription that matches everything would send every vacancy.

Every filter has one canonical form, so equal filters are stored equally and
no setting lingers unseen: `skills_match` is "any" unless at least two
skills are chosen, since with fewer it changes nothing, and whether
vacancies without a salary count exists only together with a minimum
salary (`SalaryFloor`).

Matching a vacancy against the filter lives in `domain/matching.py`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Self

from vacancy_radar.domain.choices import (
    SKILLS_MATCHES,
    FormError,
    SkillsMatch,
    known_skills,
    known_values,
    monthly_usd,
    one_of,
    skills_match,
)
from vacancy_radar.domain.seniority import LEVELS, Level
from vacancy_radar.domain.work_format import WORK_FORMATS, WorkFormat

MAX_NAME_LENGTH: Final = 80


@dataclass(frozen=True, slots=True)
class SalaryFloor:
    """A minimum monthly salary in whole US dollars."""

    min_usd: int
    # Whether vacancies that state no salary still match. Only about 5% of
    # vacancies state one, so the form suggests yes.
    include_unsalaried: bool


@dataclass(frozen=True, slots=True)
class SubscriptionFilter:
    """The criteria a vacancy is matched against, in canonical form."""

    skills: tuple[str, ...]
    # "any": the vacancy has at least one of the skills; "all": it has every one.
    skills_match: SkillsMatch
    seniority: tuple[Level, ...]
    work_formats: tuple[WorkFormat, ...]
    salary: SalaryFloor | None

    @classmethod
    def from_stored(
        cls,
        *,
        skills: Sequence[str],
        skills_match: str,
        seniority: Sequence[str],
        work_formats: Sequence[str],
        min_salary_usd: int | None,
        include_unsalaried: bool | None,
    ) -> Self:
        """Rebuild a filter from the values of a stored row.

        Raises `ValueError` for values the database constraints should have
        kept out. Skills are kept even when they have left the dictionary:
        such a skill matches no vacancy, which is safer than dropping it and
        widening the filter.
        """
        if min_salary_usd is None and include_unsalaried is None:
            salary = None
        elif min_salary_usd is not None and include_unsalaried is not None:
            salary = SalaryFloor(min_salary_usd, include_unsalaried)
        else:
            raise ValueError("a stored salary floor needs both its amount and its choice")
        return cls(
            skills=tuple(skills),
            skills_match=one_of(skills_match, SKILLS_MATCHES),
            seniority=tuple(one_of(level, LEVELS) for level in seniority),
            work_formats=tuple(one_of(fmt, WORK_FORMATS) for fmt in work_formats),
            salary=salary,
        )


@dataclass(frozen=True, slots=True)
class SubscriptionDraft:
    """A valid name and filter, ready to be stored."""

    name: str
    filter: SubscriptionFilter


@dataclass(frozen=True, slots=True)
class SubscriptionForm:
    """The form's values as entered, before any checking.

    `build_subscription` turns it into a draft, and an invalid one is shown
    again as it was typed. The defaults are what a new subscription's form
    starts with.
    """

    name: str = ""
    skills: frozenset[str] = frozenset()
    skills_match: str = "any"
    seniority: frozenset[str] = frozenset()
    work_formats: frozenset[str] = frozenset()
    min_salary_usd: str = ""
    include_unsalaried: bool = True

    @classmethod
    def of(cls, name: str, criteria: SubscriptionFilter) -> Self:
        """The form that edits a stored subscription."""
        salary = criteria.salary
        return cls(
            name=name,
            skills=frozenset(criteria.skills),
            skills_match=criteria.skills_match,
            seniority=frozenset(criteria.seniority),
            work_formats=frozenset(criteria.work_formats),
            min_salary_usd="" if salary is None else str(salary.min_usd),
            include_unsalaried=True if salary is None else salary.include_unsalaried,
        )


class InvalidSubscriptionError(FormError):
    """The form cannot become a subscription.

    The key `filter` holds an error about the criteria as a whole.
    """


def build_subscription(form: SubscriptionForm) -> SubscriptionDraft:
    """Check the form and return it in canonical form. Raises `InvalidSubscriptionError`.

    Lists come back in the order of their dictionaries. Unknown values can
    only come from a tampered form, and are refused rather than dropped.
    """
    errors: dict[str, str] = {}

    name = " ".join(form.name.split())
    if not name:
        errors["name"] = "Give the subscription a name."
    elif len(name) > MAX_NAME_LENGTH:
        errors["name"] = f"Use at most {MAX_NAME_LENGTH} characters."

    skills = known_skills(form.skills, errors)
    match = skills_match(form.skills_match, skills, errors)
    levels = known_values(form.seniority, LEVELS, "seniority", errors)
    formats = known_values(form.work_formats, WORK_FORMATS, "work_formats", errors)
    amount = monthly_usd(form.min_salary_usd, "min_salary_usd", errors)

    if not errors and not (skills or levels or formats or amount):
        errors["filter"] = "Choose at least one skill, level, work format or minimum salary."
    if errors:
        raise InvalidSubscriptionError(errors)

    return SubscriptionDraft(
        name=name,
        filter=SubscriptionFilter(
            skills=skills,
            skills_match=match,
            seniority=levels,
            work_formats=formats,
            salary=None if amount is None else SalaryFloor(amount, form.include_unsalaried),
        ),
    )
