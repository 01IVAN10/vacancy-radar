"""What a vacancy search asks for. Pure: no I/O.

The search form sends text, as a browser does: `build_search` checks it and
returns a `SearchQuery`, or raises `InvalidSearchError` with a message per
field. Fields are checked as in the subscription form (`domain/choices.py`).
Unlike a subscription, a search may be empty (it lists the newest jobs), and
its filters are strict: a vacancy whose level, work format or salary is
unknown does not pass a filter on it. A subscription waits for jobs that may
fit; a search shows what is known to fit.

How the results are shown is `domain/card.py` and `domain/snippet.py`.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

from vacancy_radar.domain.choices import (
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

Sort = Literal["relevance", "newest"]
SORTS: Final[tuple[Sort, ...]] = ("relevance", "newest")
# "First seen within the last N days."
PERIOD_DAYS: Final = (1, 3, 7, 30)

MAX_QUERY_LENGTH: Final = 200
PAGE_SIZE: Final = 20
# Deeper pages are not offered: nobody reads 1000 results, and OFFSET costs grow.
MAX_PAGE: Final = 50

# A slash between two word characters: "c/c++", "ci/cd", "Python/FastAPI".
# The same characters as in the SQL function `search_text` (migration 0016).
_WORD = "0-9A-Za-zА-ЯІЇЄҐа-яіїєґ+#"
_SLASHED: Final = re.compile(f"[{_WORD}]/[{_WORD}.]")


@dataclass(frozen=True, slots=True)
class SearchForm:
    """The search form's values as sent, before any checking.

    The defaults are an empty form, and the only place they are set: a
    missing parameter takes the default here.
    """

    q: str = ""
    skills: frozenset[str] = frozenset()
    skills_match: str = "any"
    seniority: frozenset[str] = frozenset()
    work_formats: frozenset[str] = frozenset()
    min_salary_usd: str = ""
    period: str = ""
    sources: frozenset[str] = frozenset()
    # Empty: by relevance when there are words, otherwise the newest first.
    sort: str = ""
    page: str = "1"


EMPTY_FORM: Final = SearchForm()


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A valid search. Empty tuples and None mean "any"; the defaults are an empty search."""

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
    # Always "newest" without text: relevance needs words to rank by.
    sort: Sort = "newest"
    page: int = 1

    @property
    def offset(self) -> int:
        return (self.page - 1) * PAGE_SIZE


class InvalidSearchError(FormError):
    """The form cannot become a search."""


def build_search(form: SearchForm, *, sources: tuple[str, ...]) -> SearchQuery:
    """Check the form against the known source slugs `sources`. Raises `InvalidSearchError`.

    Lists come back in the order of their dictionaries.
    """
    errors: dict[str, str] = {}

    text = " ".join(form.q.split())
    if len(text) > MAX_QUERY_LENGTH:
        errors["q"] = f"Use at most {MAX_QUERY_LENGTH} characters."
    skills = known_skills(form.skills, errors)
    match = skills_match(form.skills_match, skills, errors)
    levels = known_values(form.seniority, LEVELS, "seniority", errors)
    formats = known_values(form.work_formats, WORK_FORMATS, "work_formats", errors)
    chosen_sources = known_values(form.sources, sources, "sources", errors)
    amount = monthly_usd(form.min_salary_usd, "min_salary_usd", errors)
    period_days = _period(form.period, errors)
    sort = _sort(form.sort, has_words=bool(text), errors=errors)
    page = _page(form.page, errors)

    if errors:
        raise InvalidSearchError(errors)
    return SearchQuery(
        text=text,
        skills=skills,
        skills_match=match,
        seniority=levels,
        work_formats=formats,
        min_salary_usd=amount,
        period_days=period_days,
        sources=chosen_sources,
        sort=sort,
        page=page,
    )


def phrase_slashed_words(text: str) -> str:
    """`text` with every word that holds a slash turned into a quoted phrase.

    Search splits "c/c++" into the words "c" and "c++" (`db/fts.py`). Left
    alone, `-c/c++` would exclude "c" and then require "c++"; as `-"c/c++"`
    it excludes the two words together, and `ci/cd` finds them side by side.
    A leading `-` stays outside the quotes, and text already inside quotes is
    left as it is.
    """
    parts = text.split('"')
    # Even parts are outside quotes, an unmatched quote included.
    for index in range(0, len(parts), 2):
        parts[index] = " ".join(_phrase(token) for token in parts[index].split(" "))
    return '"'.join(parts)


def _phrase(token: str) -> str:
    if not _SLASHED.search(token):
        return token
    word = token.removeprefix("-")
    return f'{token[: len(token) - len(word)]}"{word}"'


def _period(raw: str, errors: dict[str, str]) -> int | None:
    if not raw:
        return None
    if raw not in {str(days) for days in PERIOD_DAYS}:
        errors["period"] = "Choose one of the offered periods."
        return None
    return int(raw)


def _sort(raw: str, *, has_words: bool, errors: dict[str, str]) -> Sort:
    """The order asked for; without words, always the newest first."""
    if raw and raw not in SORTS:
        errors["sort"] = "Sort by relevance or by date."
        return "newest"
    if not has_words:
        return "newest"
    return one_of(raw, SORTS) if raw else "relevance"


def _page(raw: str, errors: dict[str, str]) -> int:
    if not raw:
        return 1
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 3 and 1 <= int(raw) <= MAX_PAGE):
        errors["page"] = f"Pages go from 1 to {MAX_PAGE}."
        return 1
    return int(raw)
