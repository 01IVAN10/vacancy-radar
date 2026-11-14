"""Narrowing text to the fixed vocabularies and amounts the domain types. Pure: no I/O.

`one_of` types a value read from the database. `known_values` and
`monthly_usd` check what a form sent, collecting a message per field in
`errors` instead of raising, so a form can show every problem at once.
"""

import re
from collections.abc import Iterable
from typing import Final

# Above any monthly IT salary in Ukraine; a larger number is a typo.
MAX_MIN_SALARY_USD: Final = 100_000

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
