"""The Telegram message for one matched vacancy. Pure: no I/O.

It shows the title, the normalized fields and a link to the original posting,
never the description: public output links to the source instead of
republishing it (see CLAUDE.md, Legal data collection). Every value is
HTML-escaped, because the message is sent with `parse_mode=HTML`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Final

from vacancy_radar.domain.skills import SKILLS_BY_SLUG

# Telegram's limit on a message's text, in UTF-16 code units after entities
# are parsed. Counting the HTML instead is stricter, so a message within it
# always fits.
MAX_MESSAGE_LENGTH: Final = 4096
MAX_SKILLS: Final = 8
MAX_TITLE_LENGTH: Final = 200
MAX_COMPANY_LENGTH: Final = 100
MAX_CITIES: Final = 5

CURRENCY_SIGNS: Final = {"USD": "$", "EUR": "€", "UAH": "₴"}
PERIODS: Final = {"month": "a month", "year": "a year", "hour": "an hour"}


@dataclass(frozen=True, slots=True)
class VacancyCard:
    """What a message shows about a vacancy, as stored in `vacancy`."""

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


def render_message(card: VacancyCard, subscription_names: Sequence[str], *, site_url: str) -> str:
    """The HTML text of the message.

    The subscription names are shortened to keep it within
    `MAX_MESSAGE_LENGTH`. The rest is kept short by capping the title, the
    company, the cities and the skills. Only the link is never cut, so a URL
    of thousands of characters could still make the message too long.
    """
    lines = [f"<b>{escape(_shorten(card.title, MAX_TITLE_LENGTH))}</b>"]
    company = _shorten(card.company, MAX_COMPANY_LENGTH) if card.company else None
    cities = list(card.cities[:MAX_CITIES])
    place = ", ".join([company, *cities] if company else cities)
    if place:
        lines.append(escape(place))
    salary = format_salary(card)
    if salary:
        lines.append(f"💰 {escape(salary)}")
    details = [level.capitalize() for level in card.seniority] + [
        work_format.capitalize() for work_format in card.work_formats
    ]
    if details:
        lines.append(escape(" · ".join(details)))
    skills = [SKILLS_BY_SLUG[slug].name for slug in card.skills if slug in SKILLS_BY_SLUG]
    if skills:
        shown = ", ".join(skills[:MAX_SKILLS])
        more = len(skills) - MAX_SKILLS
        lines.append(escape(shown + (f" and {more} more" if more > 0 else "")))
    lines.append(f'<a href="{escape(card.url)}">Open on {escape(card.source_name)}</a>')
    body = "\n".join(lines)

    names = ", ".join(subscription_names)
    message = _with_footer(body, names, site_url)
    overflow = message_length(message) - MAX_MESSAGE_LENGTH
    if overflow > 0:
        # Each character cut from the plain names takes at least one code
        # unit off their escaped form, so the result fits.
        message = _with_footer(body, _shorten(names, len(names) - overflow), site_url)
    return message


def message_length(text: str) -> int:
    """The length Telegram counts: UTF-16 code units, so most emoji count as two."""
    return len(text.encode("utf-16-le")) // 2


def _with_footer(body: str, names: str, site_url: str) -> str:
    return (
        f"{body}\n\n<i>Matched: {escape(names)}</i> · "
        f'<a href="{escape(site_url)}/subscriptions">Manage subscriptions</a>'
    )


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


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"
