"""Narrowing text to the fixed vocabularies and amounts the domain types. Pure: no I/O.

`one_of` types a value read from the database. The other functions check
what a form sent, the same way for every form (subscriptions, search): each
adds a message per field to `errors` instead of raising, so a form can show
every problem at once, and the form raises a `FormError` at the end.
"""

import re
from collections.abc import Iterable
from typing import Final, Literal

from vacancy_radar.domain.skills import SKILLS, SKILLS_BY_SLUG

SkillsMatch = Literal["any", "all"]
SKILLS_MATCHES: Final[tuple[SkillsMatch, ...]] = ("any", "all")
MAX_SKILLS: Final = 20

# Above any monthly IT salary in Ukraine; a larger number is a typo.
MAX_MIN_SALARY_USD: Final = 100_000

# Position of each skill in the dictionary, so chosen skills keep its order.
_SKILL_ORDER: Final = {skill.slug: position for position, skill in enumerate(SKILLS)}

# "3000", or thousands separated by the same comma or space: "3,000", "1 000 000".
# Bounded, so no input reaches int() with more than nine digits.
_AMOUNT: Final = re.compile(r"\d{1,6}|\d{1,3}([, ])\d{3}(?:\1\d{3})?", re.ASCII)


def one_of[T: str](value: str, allowed: tuple[T, ...]) -> T:
    """`value` typed as one of `allowed`. Raises `ValueError` for anything else.

    Rows read from the database hold plain `str`. Passing them through here
    gives them their `Literal` type without a `cast` that would hide a bad value.
    """
    for candidate in allowed:
        if value == candidate:
            return candidate
    raise ValueError(f"{value!r} is not one of {allowed}")


class FormError(Exception):
    """A form that cannot be used. `errors` maps a field to its message."""

    def __init__(self, errors: dict[str, str]) -> None:
        if not errors:
            raise ValueError(f"{type(self).__name__} needs at least one field error")
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


def known_values[T: str](
    values: Iterable[str], allowed: tuple[T, ...], field: str, errors: dict[str, str]
) -> tuple[T, ...]:
    """The chosen values in the order of `allowed`; unknown ones are an error for `field`.

    Unknown values can only come from a tampered form, so they are refused
    rather than dropped.
    """
    chosen = set(values)
    unknown = sorted(chosen - set(allowed))
    if unknown:
        errors[field] = f"Unknown values: {', '.join(unknown)}."
    return tuple(value for value in allowed if value in chosen)


def monthly_usd(raw: str, field: str, errors: dict[str, str]) -> int | None:
    """A whole number of dollars a month as typed, or None when left empty.

    Accepts "3000", "3,000" and "3 000", from 1 to `MAX_MIN_SALARY_USD`;
    anything else is an error for `field`.
    """
    text = raw.strip()
    if not text:
        return None
    # The pattern caps the length, so int() never sees thousands of digits.
    if not _AMOUNT.fullmatch(text):
        errors[field] = "Enter a whole number of dollars, such as 3000."
        return None
    amount = int(text.replace(",", "").replace(" ", ""))
    if not 1 <= amount <= MAX_MIN_SALARY_USD:
        errors[field] = f"Enter an amount from 1 to {MAX_MIN_SALARY_USD:,}."
        return None
    return amount


def known_skills(values: Iterable[str], errors: dict[str, str]) -> tuple[str, ...]:
    """The chosen skill slugs in dictionary order, at most `MAX_SKILLS` (field `skills`)."""
    chosen = set(values)
    unknown = sorted(chosen - SKILLS_BY_SLUG.keys())
    skills = tuple(sorted(chosen & SKILLS_BY_SLUG.keys(), key=_SKILL_ORDER.__getitem__))
    if unknown:
        errors["skills"] = f"Unknown skills: {', '.join(unknown)}."
    elif len(skills) > MAX_SKILLS:
        errors["skills"] = f"Choose at most {MAX_SKILLS} skills."
    return skills


def skills_match(raw: str, skills: tuple[str, ...], errors: dict[str, str]) -> SkillsMatch:
    """Whether any or all of `skills` must match (field `skills_match`).

    With fewer than two skills the choice changes nothing, so it is "any":
    equal filters always look the same.
    """
    if raw not in SKILLS_MATCHES:
        errors["skills_match"] = "Choose whether any or all of the skills must match."
        return "any"
    return one_of(raw, SKILLS_MATCHES) if len(skills) > 1 else "any"
