from typing import Any

import pytest

from vacancy_radar.domain.choices import MAX_MIN_SALARY_USD, MAX_SKILLS, FormError
from vacancy_radar.domain.search import (
    EMPTY_FORM,
    MAX_PAGE,
    MAX_QUERY_LENGTH,
    PAGE_SIZE,
    InvalidSearchError,
    SearchForm,
    SearchQuery,
    build_search,
    phrase_slashed_words,
)
from vacancy_radar.domain.skills import SKILLS

SOURCES = ("djinni", "dou")


def build(**fields: Any) -> SearchQuery:
    return build_search(SearchForm(**fields), sources=SOURCES)


def errors_of(**fields: Any) -> dict[str, str]:
    with pytest.raises(InvalidSearchError) as caught:
        build(**fields)
    return caught.value.errors


def test_an_empty_form_lists_the_newest_jobs() -> None:
    assert build() == SearchQuery()


def test_a_full_form_in_canonical_form() -> None:
    query = build(
        q="  python   django ",
        skills=frozenset({"django", "python"}),
        skills_match="all",
        seniority=frozenset({"senior", "middle"}),
        work_formats=frozenset({"remote"}),
        min_salary_usd="3,000",
        period="7",
        sources=frozenset({"dou"}),
        sort="newest",
        page="2",
    )

    assert query == SearchQuery(
        text="python django",
        skills=("python", "django"),
        skills_match="all",
        seniority=("middle", "senior"),
        work_formats=("remote",),
        min_salary_usd=3000,
        period_days=7,
        sources=("dou",),
        sort="newest",
        page=2,
    )
    assert query.offset == PAGE_SIZE


def test_words_sort_by_relevance_unless_asked_otherwise() -> None:
    assert build(q="python").sort == "relevance"
    assert build(q="python", sort="newest").sort == "newest"


def test_without_words_relevance_means_newest() -> None:
    assert build(sort="relevance").sort == "newest"


def test_one_skill_always_matches_any() -> None:
    assert build(skills=frozenset({"python"}), skills_match="all").skills_match == "any"


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"q": "x" * (MAX_QUERY_LENGTH + 1)}, "q"),
        ({"skills": frozenset({"python", "cobol-9000"})}, "skills"),
        ({"skills_match": "some"}, "skills_match"),
        ({"seniority": frozenset({"guru"})}, "seniority"),
        ({"work_formats": frozenset({"beach"})}, "work_formats"),
        ({"sources": frozenset({"workua"})}, "sources"),
        ({"min_salary_usd": "a lot"}, "min_salary_usd"),
        ({"min_salary_usd": str(MAX_MIN_SALARY_USD + 1)}, "min_salary_usd"),
        ({"period": "2"}, "period"),
        ({"period": "week"}, "period"),
        ({"sort": "salary"}, "sort"),
        ({"page": "0"}, "page"),
        ({"page": str(MAX_PAGE + 1)}, "page"),
        ({"page": "-1"}, "page"),
        ({"page": "١"}, "page"),
        ({"page": "9" * 400}, "page"),
    ],
)
def test_refuses_what_the_form_never_offers(fields: dict[str, Any], field: str) -> None:
    assert field in errors_of(**fields)


def test_too_many_skills() -> None:
    skills = frozenset(skill.slug for skill in SKILLS[: MAX_SKILLS + 1])

    assert errors_of(skills=skills) == {"skills": f"Choose at most {MAX_SKILLS} skills."}


def test_reports_every_field_at_once() -> None:
    assert errors_of(period="2", page="0", sort="?").keys() == {"period", "page", "sort"}


def test_a_query_at_the_limit_is_kept() -> None:
    assert build(q="x" * MAX_QUERY_LENGTH).text == "x" * MAX_QUERY_LENGTH


def test_an_empty_page_is_the_first() -> None:
    assert build(page="").page == 1


def test_an_error_needs_a_field() -> None:
    with pytest.raises(ValueError, match="InvalidSearchError needs at least one field error"):
        InvalidSearchError({})


def test_search_errors_are_form_errors() -> None:
    error = InvalidSearchError({"page": "Pages go from 1 to 50."})

    assert isinstance(error, FormError)
    assert str(error) == "page: Pages go from 1 to 50."


def test_the_empty_form_is_the_empty_search() -> None:
    assert build_search(EMPTY_FORM, sources=SOURCES) == SearchQuery()


@pytest.mark.parametrize(
    ("q", "sort", "expected"),
    [
        ("", "", "newest"),
        ("", "relevance", "newest"),
        ("", "newest", "newest"),
        ("python", "", "relevance"),
        ("python", "relevance", "relevance"),
        ("python", "newest", "newest"),
    ],
)
def test_sort(q: str, sort: str, expected: str) -> None:
    assert build(q=q, sort=sort).sort == expected


@pytest.mark.parametrize(
    ("typed", "sent"),
    [
        ("python", "python"),
        ("ci/cd", '"ci/cd"'),
        ("python -c/c++", 'python -"c/c++"'),
        ("-ci/cd or go", '-"ci/cd" or go'),
        ("Python/FastAPI django", '"Python/FastAPI" django'),
        # Already a phrase, or inside an unclosed quote: left alone.
        ('"python/django" sql', '"python/django" sql'),
        ('sql "a b/c', 'sql "a b/c'),
        # Not between two words.
        ("a / b", "a / b"),
        ("/path", "/path"),
        ("24/7", '"24/7"'),
    ],
)
def test_a_word_with_a_slash_becomes_a_phrase(typed: str, sent: str) -> None:
    assert phrase_slashed_words(typed) == sent
