"""What a subscription asks for, and the rules a new or edited one must follow. Pure: no I/O.

A subscription is a filter over normalized vacancies. An empty criterion
means "any": a subscription with only `seniority = ("senior",)` wants every
senior vacancy, whatever its skills. At least one criterion must be set,
because a subscription that matches everything would send every vacancy.

Matching a vacancy against the filter comes in stage 7.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from vacancy_radar.domain.seniority import LEVELS, Level
from vacancy_radar.domain.skills import SKILLS, SKILLS_BY_SLUG
from vacancy_radar.domain.work_format import WORK_FORMATS, WorkFormat

SkillsMatch = Literal["any", "all"]
SKILLS_MATCHES: Final[tuple[SkillsMatch, ...]] = ("any", "all")

MAX_NAME_LENGTH: Final = 80
MAX_SKILLS: Final = 20
# Above any monthly IT salary in Ukraine; a larger number is a typo.
MAX_MIN_SALARY_USD: Final = 100_000

# Position of each skill in the dictionary, so skills are stored in its order.
_SKILL_ORDER: Final = {skill.slug: position for position, skill in enumerate(SKILLS)}


@dataclass(frozen=True, slots=True)
class SubscriptionFilter:
    """The criteria a vacancy is matched against."""

    skills: tuple[str, ...]
    # "any": the vacancy has at least one of the skills; "all": it has every one.
    skills_match: SkillsMatch
    seniority: tuple[Level, ...]
    work_formats: tuple[WorkFormat, ...]
    min_salary_usd: int | None
    # With a minimum salary: whether vacancies that state no salary still match.
    # Only about 5% of vacancies state one, so the default is yes.
    include_unsalaried: bool


@dataclass(frozen=True, slots=True)
class SubscriptionDraft:
    """A valid name and filter, ready to be stored."""

    name: str
    filter: SubscriptionFilter


class InvalidSubscriptionError(Exception):
    """The form cannot become a subscription. `errors` maps a field to its message.

    The key `filter` holds an error about the criteria as a whole.
    """

    def __init__(self, errors: dict[str, str]) -> None:
        if not errors:
            raise ValueError("an InvalidSubscriptionError needs at least one field error")
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


def build_subscription(
    *,
    name: str,
    skills: Sequence[str],
    skills_match: str,
    seniority: Sequence[str],
    work_formats: Sequence[str],
    min_salary_usd: str,
    include_unsalaried: bool,
) -> SubscriptionDraft:
    """Check the raw form values and return them cleaned. Raises `InvalidSubscriptionError`.

    Lists come back without duplicates, in the order of their dictionaries,
    so equal filters are stored equally. Unknown values can only come from a
    tampered form, and are refused rather than dropped.
    """
    errors: dict[str, str] = {}

    clean_name = " ".join(name.split())
    if not clean_name:
        errors["name"] = "Give the subscription a name."
    elif len(clean_name) > MAX_NAME_LENGTH:
        errors["name"] = f"Use at most {MAX_NAME_LENGTH} characters."

    unknown_skills = sorted(set(skills) - SKILLS_BY_SLUG.keys())
    clean_skills = tuple(sorted(set(skills) & SKILLS_BY_SLUG.keys(), key=_SKILL_ORDER.__getitem__))
    if unknown_skills:
        errors["skills"] = f"Unknown skills: {', '.join(unknown_skills)}."
    elif len(clean_skills) > MAX_SKILLS:
        errors["skills"] = f"Choose at most {MAX_SKILLS} skills."

    match = _skills_match(skills_match, errors)
    clean_levels = _known(seniority, LEVELS, "seniority", errors)
    clean_formats = _known(work_formats, WORK_FORMATS, "work_formats", errors)
    salary = _min_salary(min_salary_usd, errors)

    if not errors and not (clean_skills or clean_levels or clean_formats or salary):
        errors["filter"] = "Choose at least one skill, level, work format or minimum salary."
    if errors:
        raise InvalidSubscriptionError(errors)

    return SubscriptionDraft(
        name=clean_name,
        filter=SubscriptionFilter(
            skills=clean_skills,
            skills_match=match,
            seniority=clean_levels,
            work_formats=clean_formats,
            min_salary_usd=salary,
            include_unsalaried=include_unsalaried,
        ),
    )


def _skills_match(raw: str, errors: dict[str, str]) -> SkillsMatch:
    for match in SKILLS_MATCHES:
        if raw == match:
            return match
    errors["skills_match"] = "Choose whether any or all of the skills must match."
    return "any"


def _known[T: str](
    values: Sequence[str], allowed: tuple[T, ...], field: str, errors: dict[str, str]
) -> tuple[T, ...]:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        errors[field] = f"Unknown values: {', '.join(unknown)}."
    return tuple(value for value in allowed if value in values)


def _min_salary(raw: str, errors: dict[str, str]) -> int | None:
    text = raw.strip().replace(" ", "").replace(",", "")
    if not text:
        return None
    if not text.isascii() or not text.isdigit():
        errors["min_salary_usd"] = "Enter a whole number of dollars, such as 3000."
        return None
    salary = int(text)
    if not 1 <= salary <= MAX_MIN_SALARY_USD:
        errors["min_salary_usd"] = f"Enter an amount from 1 to {MAX_MIN_SALARY_USD:,}."
        return None
    return salary
