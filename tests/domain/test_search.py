from typing import Any

import pytest

from vacancy_radar.domain.choices import MAX_MIN_SALARY_USD
from vacancy_radar.domain.search import (
    EXCERPT_LENGTH,
    HIGHLIGHT_START,
    HIGHLIGHT_STOP,
    MAX_PAGE,
    MAX_QUERY_LENGTH,
    PAGE_SIZE,
    InvalidSearchError,
    SearchForm,
    SearchQuery,
    build_search,
    excerpt,
    snippet_html,
)
from vacancy_radar.domain.skills import SKILLS
from vacancy_radar.domain.subscriptions import MAX_SKILLS

SOURCES = ("djinni", "dou")


def build(**fields: Any) -> SearchQuery:
    return build_search(SearchForm(**fields), sources=SOURCES)


def errors_of(**fields: Any) -> dict[str, str]:
    with pytest.raises(InvalidSearchError) as caught:
        build(**fields)
    return caught.value.errors


def test_an_empty_form_lists_the_newest_jobs() -> None:
    assert build() == SearchQuery(sort="newest", page=1)


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
    with pytest.raises(ValueError, match="at least one"):
        InvalidSearchError({})


def mark(text: str) -> str:
    return f"{HIGHLIGHT_START}{text}{HIGHLIGHT_STOP}"


def test_snippet_marks_the_highlighted_words() -> None:
    assert snippet_html(f"Know {mark('Python')} and {mark('SQL')}") == (
        "Know <mark>Python</mark> and <mark>SQL</mark>"
    )


def test_snippet_escapes_the_text_and_its_highlights() -> None:
    text = f"<script>alert(1)</script> & {mark('<b>Go</b>')}"

    assert snippet_html(text) == (
        "&lt;script&gt;alert(1)&lt;/script&gt; &amp; <mark>&lt;b&gt;Go&lt;/b&gt;</mark>"
    )


def test_snippet_drops_markers_without_a_pair() -> None:
    assert snippet_html(f"a{HIGHLIGHT_START}b {mark('c')} d{HIGHLIGHT_STOP}") == (
        "ab <mark>c</mark> d"
    )


def test_excerpt_keeps_a_short_text_whole() -> None:
    assert excerpt("Short text.") == "Short text."


def test_excerpt_cuts_a_long_text_at_a_word() -> None:
    text = "word, " * 100

    cut = excerpt(text)

    assert len(cut) <= EXCERPT_LENGTH
    assert cut.endswith("word…")
    assert text.startswith(cut.removesuffix("…"))


def test_excerpt_cuts_one_endless_word() -> None:
    assert excerpt("x" * 50, limit=10) == "x" * 9 + "…"
