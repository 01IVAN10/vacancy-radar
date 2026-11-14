"""What a vacancy shows wherever it is shown: Telegram messages and site pages. Pure: no I/O.

A card has the title, the normalized fields and a link to the original
posting, never the full description: public output links to the source
instead of republishing it (CLAUDE.md, Legal data collection). Values are
plain text; each output escapes them for its own format.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from vacancy_radar.domain.skills import SKILLS_BY_SLUG

CURRENCY_SIGNS: Final = {"USD": "$", "EUR": "€", "UAH": "₴"}
PERIODS: Final = {"month": "a month", "year": "a year", "hour": "an hour"}


@dataclass(frozen=True, slots=True)
class VacancyCard:
    """What is shown about a vacancy, as stored in `vacancy`."""

    title: str
    url: str
    source_name: str
    company: str | None
    cities: Sequence[str]
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    salary_period: str | None
    seniority: Sequence[str]
    work_formats: Sequence[str]
    skills: Sequence[str]


def format_salary(card: VacancyCard) -> str | None:
    """The salary as the vacancy states it, such as "$3000-4500 a month"."""
    if card.salary_currency is None or card.salary_period is None:
        return None
    sign = CURRENCY_SIGNS.get(card.salary_currency, f"{card.salary_currency} ")
    match card.salary_min, card.salary_max:
        case int(low), int(high) if low != high:
            amount = f"{sign}{low}-{high}"
        case int(low), int():
            amount = f"{sign}{low}"
        case int(low), None:
            amount = f"from {sign}{low}"
        case None, int(high):
            amount = f"up to {sign}{high}"
        case _:
            return None
    return f"{amount} {PERIODS.get(card.salary_period, card.salary_period)}"


def place(company: str | None, cities: Sequence[str]) -> str:
    """Where the job is: "Acme, Kyiv, Lviv", or only the cities, or only the company."""
    return ", ".join([company, *cities] if company else cities)


def details(card: VacancyCard) -> list[str]:
    """Its levels, then its work formats, as words: ["Senior", "Remote"]."""
    return [level.capitalize() for level in card.seniority] + [
        work_format.capitalize() for work_format in card.work_formats
    ]


def skill_names(card: VacancyCard) -> list[str]:
    """Its skills by their dictionary names, leaving out any no longer in the dictionary."""
    return [SKILLS_BY_SLUG[slug].name for slug in card.skills if slug in SKILLS_BY_SLUG]
