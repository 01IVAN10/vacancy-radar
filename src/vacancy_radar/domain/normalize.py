"""From a raw feed item to a normalized vacancy. Pure: no I/O.

Each source has its own mapping, because its feed packs the fields
differently: DOU puts company, salary, places and work format into the
title, while Djinni has only free text plus a category. Both end in the same
`NormalizedVacancy`.

Raising `NORMALIZER_VERSION` after a parser changes makes the reprocessing
task rebuild every stored vacancy from `raw_vacancy`.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from vacancy_radar.domain.dou_title import parse_dou_title
from vacancy_radar.domain.salary import Salary, find_salary
from vacancy_radar.domain.seniority import Level, detect_seniority
from vacancy_radar.domain.skills import extract_skills
from vacancy_radar.domain.text import html_to_text, normalize_text
from vacancy_radar.domain.work_format import WorkFormat, detect_work_formats

NORMALIZER_VERSION: Final = 2

# Both sources publish in Kyiv time, and NBU sets its rates per Kyiv day.
KYIV = ZoneInfo("Europe/Kyiv")

Payload = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class NormalizedVacancy:
    title: str
    company: str | None
    cities: tuple[str, ...]
    # Djinni's primary keyword ("Python", "Sales Manager"); DOU's feed has none.
    category: str | None
    published_at: datetime | None
    salary: Salary | None
    seniority: tuple[Level, ...]
    work_formats: tuple[WorkFormat, ...]
    skills: tuple[str, ...]


def normalize(source: str, payload: Payload) -> NormalizedVacancy:
    """Normalize one `raw_vacancy.payload` of the given source slug.

    Raises `KeyError` for a source without a mapping.
    """
    return NORMALIZERS[source](payload)


def normalize_djinni(payload: Payload) -> NormalizedVacancy:
    title = normalize_text(_text(payload, "title"))
    text = html_to_text(_text(payload, "description"))
    categories = payload.get("categories")
    category = categories[0] if isinstance(categories, list) and categories else None
    return NormalizedVacancy(
        title=title,
        company=None,  # not in the feed: docs/sources.md, open question 2
        cities=(),
        category=category if isinstance(category, str) and category else None,
        published_at=parse_pub_date(payload.get("pubDate")),
        salary=find_salary(text),
        seniority=detect_seniority(title),
        work_formats=detect_work_formats(title, text),
        skills=extract_skills(f"{title} {text}"),
    )


def normalize_dou(payload: Payload) -> NormalizedVacancy:
    parts = parse_dou_title(normalize_text(_text(payload, "title")))
    text = html_to_text(_text(payload, "description"))
    return NormalizedVacancy(
        title=parts.position,
        company=parts.company,
        cities=parts.cities,
        category=None,
        published_at=parse_pub_date(payload.get("pubDate")),
        # The title's salary is a structured field; the text is only a fallback.
        salary=parts.salary or find_salary(text),
        seniority=detect_seniority(parts.position),
        work_formats=detect_work_formats(parts.position, text, flagged=parts.work_formats),
        skills=extract_skills(f"{parts.position} {text}"),
    )


NORMALIZERS: Final[dict[str, Callable[[Payload], NormalizedVacancy]]] = {
    "djinni": normalize_djinni,
    "dou": normalize_dou,
}


def parse_pub_date(value: object) -> datetime | None:
    """An RFC 822 `pubDate` as an aware datetime, or `None` if it is unusable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def kyiv_date(moment: datetime) -> date:
    """The calendar day in Kyiv: the day whose NBU rate applies."""
    return moment.astimezone(KYIV).date()


def _text(payload: Payload, key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""
