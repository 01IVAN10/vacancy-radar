"""What a vacancy search asks for, and how its results are shown. Pure: no I/O.

The search form sends text, as a browser does: `build_search` checks it and
returns a `SearchQuery`, or raises `InvalidSearchError` with a message per
field. Unlike a subscription, a search may be empty (it lists the newest
jobs), and its filters are strict: a vacancy whose level, work format or
salary is unknown does not pass a filter on it. A subscription waits for
jobs that may fit; a search shows what is known to fit.

Results show a short excerpt of the description, never all of it
(CLAUDE.md, Legal data collection). `snippet_html` is the only place where
vacancy text becomes HTML, and it escapes everything but its own highlights.
"""

import re
from dataclasses import dataclass
from html import escape
from typing import Final, Literal

from vacancy_radar.domain.choices import known_values, monthly_usd, one_of
from vacancy_radar.domain.seniority import LEVELS, Level
from vacancy_radar.domain.skills import SKILLS, SKILLS_BY_SLUG
from vacancy_radar.domain.subscriptions import MAX_SKILLS, SKILLS_MATCHES, SkillsMatch
from vacancy_radar.domain.work_format import WORK_FORMATS, WorkFormat

Sort = Literal["relevance", "newest"]
SORTS: Final[tuple[Sort, ...]] = ("relevance", "newest")
# "First seen within the last N days."
PERIOD_DAYS: Final = (1, 3, 7, 30)

MAX_QUERY_LENGTH: Final = 200
PAGE_SIZE: Final = 20
# Deeper pages are not offered: nobody reads 1000 results, and OFFSET costs grow.
MAX_PAGE: Final = 50
# The longest excerpt shown, in characters, when no words are highlighted.
EXCERPT_LENGTH: Final = 300

# Postgres's `ts_headline` puts these around the words it highlights. They
# are control characters, which normalized vacancy text never contains, and
# `snippet_html` turns them into `<mark>` only after escaping the text.
HIGHLIGHT_START: Final = "\x02"
HIGHLIGHT_STOP: Final = "\x03"
_HIGHLIGHTED: Final = re.compile(
    f"{HIGHLIGHT_START}([^{HIGHLIGHT_START}{HIGHLIGHT_STOP}]*){HIGHLIGHT_STOP}"
)
_MARKERS: Final = re.compile(f"[{HIGHLIGHT_START}{HIGHLIGHT_STOP}]")

_SKILL_ORDER: Final = {skill.slug: position for position, skill in enumerate(SKILLS)}


@dataclass(frozen=True, slots=True)
class SearchForm:
    """The search form's values as sent, before any checking. The defaults are an empty form."""

    q: str = ""
    skills: frozenset[str] = frozenset()
    skills_match: str = "any"
    seniority: frozenset[str] = frozenset()
    work_formats: frozenset[str] = frozenset()
    min_salary_usd: str = ""
    period: str = ""
    sources: frozenset[str] = frozenset()
    sort: str = ""
    page: str = "1"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A valid search. Empty tuples and None mean "any"."""

    text: str = ""
    skills: tuple[str, ...] = ()
    # "any": the vacancy has at least one of the skills; "all": it has every one.
    skills_match: SkillsMatch = "any"
    seniority: tuple[Level, ...] = ()
    work_formats: tuple[WorkFormat, ...] = ()
    # The top of the vacancy's monthly dollar range must reach it.
    min_salary_usd: int | None = None
    # Jobs first seen within this many days.
    period_days: int | None = None
    # Source slugs.
    sources: tuple[str, ...] = ()
    sort: Sort = "newest"
    page: int = 1

    @property
    def offset(self) -> int:
        return (self.page - 1) * PAGE_SIZE


class InvalidSearchError(Exception):
    """The form cannot become a search. `errors` maps a field to its message."""

    def __init__(self, errors: dict[str, str]) -> None:
        if not errors:
            raise ValueError("an InvalidSearchError needs at least one field error")
        self.errors = errors
        super().__init__("; ".join(f"{field}: {message}" for field, message in errors.items()))


def build_search(form: SearchForm, *, sources: tuple[str, ...]) -> SearchQuery:
    """Check the form against the known `sources`. Raises `InvalidSearchError`.

    Lists come back in the order of their dictionaries. Sorting by relevance
    needs words to rank by, so without them the newest jobs come first.
    """
    errors: dict[str, str] = {}

    text = " ".join(form.q.split())
    if len(text) > MAX_QUERY_LENGTH:
        errors["q"] = f"Use at most {MAX_QUERY_LENGTH} characters."

    unknown_skills = sorted(form.skills - SKILLS_BY_SLUG.keys())
    skills = tuple(sorted(form.skills & SKILLS_BY_SLUG.keys(), key=_SKILL_ORDER.__getitem__))
    if unknown_skills:
        errors["skills"] = f"Unknown skills: {', '.join(unknown_skills)}."
    elif len(skills) > MAX_SKILLS:
        errors["skills"] = f"Choose at most {MAX_SKILLS} skills."
    if form.skills_match not in SKILLS_MATCHES:
        errors["skills_match"] = "Choose whether any or all of the skills must match."

    levels = known_values(form.seniority, LEVELS, "seniority", errors)
    formats = known_values(form.work_formats, WORK_FORMATS, "work_formats", errors)
    chosen_sources = known_values(form.sources, sources, "sources", errors)
    amount = monthly_usd(form.min_salary_usd, "min_salary_usd", errors)
    period_days = _period(form.period, errors)
    sort = _sort(form.sort, errors)
    page = _page(form.page, errors)

    if errors:
        raise InvalidSearchError(errors)
    return SearchQuery(
        text=text,
        skills=skills,
        skills_match=one_of(form.skills_match, SKILLS_MATCHES) if len(skills) > 1 else "any",
        seniority=levels,
        work_formats=formats,
        min_salary_usd=amount,
        period_days=period_days,
        sources=chosen_sources,
        sort=sort if text else "newest",
        page=page,
    )


def snippet_html(text: str) -> str:
    """HTML for a piece of vacancy text, with `ts_headline`'s highlights as `<mark>`.

    Everything is escaped first, so the text cannot add markup of its own,
    and markers left without a pair are dropped.
    """
    marked = _HIGHLIGHTED.sub(r"<mark>\1</mark>", escape(text))
    return _MARKERS.sub("", marked)


def excerpt(text: str, limit: int = EXCERPT_LENGTH) -> str:
    """The beginning of `text`, cut at a word boundary within `limit` characters."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip(" ,.;:-–—") + "…"


def _period(raw: str, errors: dict[str, str]) -> int | None:
    if not raw:
        return None
    if raw not in {str(days) for days in PERIOD_DAYS}:
        errors["period"] = "Choose one of the offered periods."
        return None
    return int(raw)


def _sort(raw: str, errors: dict[str, str]) -> Sort:
    if not raw:
        return "relevance"
    if raw not in SORTS:
        errors["sort"] = "Sort by relevance or by date."
        return "relevance"
    return one_of(raw, SORTS)


def _page(raw: str, errors: dict[str, str]) -> int:
    if not raw:
        return 1
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 3 and 1 <= int(raw) <= MAX_PAGE):
        errors["page"] = f"Pages go from 1 to {MAX_PAGE}."
        return 1
    return int(raw)
