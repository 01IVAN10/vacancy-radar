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

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Self

from vacancy_radar.domain.seniority import LEVELS, Level
from vacancy_radar.domain.skills import SKILLS, SKILLS_BY_SLUG
from vacancy_radar.domain.work_format import WORK_FORMATS, WorkFormat

SkillsMatch = Literal["any", "all"]
SKILLS_MATCHES: Final[tuple[SkillsMatch, ...]] = ("any", "all")

MAX_NAME_LENGTH: Final = 80
MAX_SKILLS: Final = 20
# Above any monthly IT salary in Ukraine; a larger number is a typo.
MAX_MIN_SALARY_USD: Final = 100_000

# "3000", or thousands separated by the same comma or space: "3,000", "1 000 000".
# Bounded, so no input reaches int() with more than nine digits.
_AMOUNT: Final = re.compile(r"\d{1,6}|\d{1,3}([, ])\d{3}(?:\1\d{3})?", re.ASCII)

# Position of each skill in the dictionary, so skills are stored in its order.
_SKILL_ORDER: Final = {skill.slug: position for position, skill in enumerate(SKILLS)}


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
            skills_match=_one_of(skills_match, SKILLS_MATCHES),
            seniority=tuple(_one_of(level, LEVELS) for level in seniority),
            work_formats=tuple(_one_of(fmt, WORK_FORMATS) for fmt in work_formats),
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


class InvalidSubscriptionError(Exception):
    """The form cannot become a subscription. `errors` maps a field to its message.

    The key `filter` holds an error about the criteria as a whole.
    """

    def __init__(self, errors: dict[str, str]) -> None:
        if not errors:
            raise ValueError("an InvalidSubscriptionError needs at least one field error")
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


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

    unknown_skills = sorted(form.skills - SKILLS_BY_SLUG.keys())
    skills = tuple(sorted(form.skills & SKILLS_BY_SLUG.keys(), key=_SKILL_ORDER.__getitem__))
    if unknown_skills:
        errors["skills"] = f"Unknown skills: {', '.join(unknown_skills)}."
    elif len(skills) > MAX_SKILLS:
        errors["skills"] = f"Choose at most {MAX_SKILLS} skills."

    if form.skills_match not in SKILLS_MATCHES:
        errors["skills_match"] = "Choose whether any or all of the skills must match."
    levels = _known(form.seniority, LEVELS, "seniority", errors)
    formats = _known(form.work_formats, WORK_FORMATS, "work_formats", errors)
    amount = _amount(form.min_salary_usd, errors)

    if not errors and not (skills or levels or formats or amount):
        errors["filter"] = "Choose at least one skill, level, work format or minimum salary."
    if errors:
        raise InvalidSubscriptionError(errors)

    return SubscriptionDraft(
        name=name,
        filter=SubscriptionFilter(
            skills=skills,
            skills_match=_one_of(form.skills_match, SKILLS_MATCHES) if len(skills) > 1 else "any",
            seniority=levels,
            work_formats=formats,
            salary=None if amount is None else SalaryFloor(amount, form.include_unsalaried),
        ),
    )


def _one_of[T: str](value: str, allowed: tuple[T, ...]) -> T:
    """`value` typed as one of `allowed`. Raises `ValueError` for anything else."""
    for candidate in allowed:
        if value == candidate:
            return candidate
    raise ValueError(f"{value!r} is not one of {allowed}")


def _known[T: str](
    values: Iterable[str], allowed: tuple[T, ...], field: str, errors: dict[str, str]
) -> tuple[T, ...]:
    chosen = set(values)
    unknown = sorted(chosen - set(allowed))
    if unknown:
        errors[field] = f"Unknown values: {', '.join(unknown)}."
    return tuple(value for value in allowed if value in chosen)


def _amount(raw: str, errors: dict[str, str]) -> int | None:
    text = raw.strip()
    if not text:
        return None
    # The pattern caps the length, so int() never sees thousands of digits.
    if not _AMOUNT.fullmatch(text):
        errors["min_salary_usd"] = "Enter a whole number of dollars, such as 3000."
        return None
    amount = int(text.replace(",", "").replace(" ", ""))
    if not 1 <= amount <= MAX_MIN_SALARY_USD:
        errors["min_salary_usd"] = f"Enter an amount from 1 to {MAX_MIN_SALARY_USD:,}."
        return None
    return amount
