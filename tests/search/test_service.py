from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import NOW, add_clustered_vacancy, add_vacancy
from vacancy_radar.db.models import Vacancy
from vacancy_radar.domain.search import MAX_PAGE, PAGE_SIZE, SearchQuery
from vacancy_radar.search.service import Posting, SearchPage, search_jobs

pytestmark = pytest.mark.anyio


async def job(session: AsyncSession, **fields: Any) -> Vacancy:
    """A vacancy in a cluster of its own."""
    return await session.run_sync(lambda sync: add_clustered_vacancy(sync, **fields))


async def posting(session: AsyncSession, of: Vacancy, **fields: Any) -> Vacancy:
    """Another vacancy of the same job as `of`."""
    return await session.run_sync(
        lambda sync: add_vacancy(sync, cluster_id=of.cluster_id, **fields)
    )


async def search(session: AsyncSession, **fields: Any) -> SearchPage:
    return await search_jobs(session, SearchQuery(**fields), now=NOW)


async def titles(session: AsyncSession, **fields: Any) -> list[str]:
    return [result.card.title for result in (await search(session, **fields)).results]


async def test_finds_jobs_by_their_words(async_session: AsyncSession) -> None:
    await job(async_session, title="Python Developer")
    await job(async_session, title="Java Developer")

    assert await titles(async_session, text="python", sort="relevance") == ["Python Developer"]


async def test_a_job_on_two_sites_is_one_result_with_both_links(
    async_session: AsyncSession,
) -> None:
    first = await job(async_session, title="Data Engineer", source="djinni", company=None)
    second = await posting(async_session, first, title="Data Engineer", source="dou")

    page = await search(async_session, text="data engineer")

    assert page.total == 1
    [result] = page.results
    assert result.postings == (
        Posting("Djinni", first.url),
        Posting("DOU", second.url),
    )
    # DOU names the company, so its vacancy is the more complete one.
    assert result.card.url == second.url


async def test_the_shown_vacancy_is_one_that_passes_the_filters(
    async_session: AsyncSession,
) -> None:
    fuller = await job(
        async_session, source="dou", salary_min=4000, salary_currency="USD", salary_period="month"
    )
    other = await posting(async_session, fuller, source="djinni")

    [result] = (await search(async_session, sources=("djinni",))).results

    assert result.card.url == other.url
    assert len(result.postings) == 2


async def test_the_most_complete_vacancy_shows_the_job(async_session: AsyncSession) -> None:
    first = await job(async_session, company=None)
    with_salary = await posting(
        async_session,
        first,
        company=None,
        salary_min=3000,
        salary_max=4000,
        salary_currency="USD",
        salary_period="month",
    )

    [result] = (await search(async_session)).results

    assert result.card.url == with_salary.url


async def test_a_vacancy_without_a_cluster_is_not_shown(async_session: AsyncSession) -> None:
    await async_session.run_sync(lambda sync: add_vacancy(sync, title="Waiting"))

    assert await titles(async_session) == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"skills": ("python", "sql")}, ["Python and SQL", "Python only"]),
        ({"skills": ("python", "sql"), "skills_match": "all"}, ["Python and SQL"]),
        ({"seniority": ("senior",)}, ["Python and SQL"]),
        ({"work_formats": ("remote", "hybrid")}, ["Python only"]),
        # The top of the range counts, and a lower bound alone is the top.
        ({"min_salary_usd": 5000}, ["Python and SQL", "Python only"]),
        ({"min_salary_usd": 5001}, ["Python only"]),
        ({"sources": ("dou",)}, ["Python only"]),
        ({"period_days": 1}, ["Python and SQL"]),
    ],
)
async def test_filters_are_strict(
    async_session: AsyncSession, query: dict[str, Any], expected: list[str]
) -> None:
    await job(
        async_session,
        title="Python and SQL",
        skills=["python", "sql"],
        seniority=["senior"],
        salary_min=4000,
        salary_max=5000,
        salary_currency="USD",
        salary_period="month",
        salary_usd_min=4000,
        salary_usd_max=5000,
        first_seen_at=NOW - timedelta(hours=1),
    )
    await job(
        async_session,
        title="Python only",
        source="dou",
        work_formats=["remote"],
        salary_min=6000,
        salary_currency="USD",
        salary_period="month",
        salary_usd_min=6000,
        first_seen_at=NOW - timedelta(days=2),
    )
    # Unknown level, format and salary: no strict filter on them lets it through.
    await job(
        async_session, title="Nothing known", skills=[], first_seen_at=NOW - timedelta(days=3)
    )

    assert sorted(await titles(async_session, **query)) == expected


async def test_newest_first(async_session: AsyncSession) -> None:
    await job(
        async_session, title="Older", description="developer", first_seen_at=NOW - timedelta(days=2)
    )
    await job(
        async_session,
        title="Newer",
        description="developer",
        first_seen_at=NOW - timedelta(hours=1),
    )

    assert await titles(async_session, text="developer", sort="newest") == ["Newer", "Older"]
    assert await titles(async_session) == ["Newer", "Older"]


async def test_most_relevant_first(async_session: AsyncSession) -> None:
    await job(
        async_session,
        title="Developer",
        description="Kotlin and more",
        first_seen_at=NOW - timedelta(hours=1),
    )
    await job(async_session, title="Kotlin Developer", first_seen_at=NOW - timedelta(days=2))

    assert await titles(async_session, text="kotlin", sort="relevance") == [
        "Kotlin Developer",
        "Developer",
    ]


async def test_pages(async_session: AsyncSession) -> None:
    for hours in range(PAGE_SIZE + 3):
        await job(async_session, title=f"Job {hours}", first_seen_at=NOW - timedelta(hours=hours))

    first = await search(async_session)
    second = await search(async_session, page=2)
    past = await search(async_session, page=3)

    assert (first.total, first.pages, len(first.results)) == (PAGE_SIZE + 3, 2, PAGE_SIZE)
    assert [r.card.title for r in second.results] == [f"Job {h}" for h in range(PAGE_SIZE, 23)]
    assert (past.total, past.results) == (PAGE_SIZE + 3, ())


def test_pages_stop_at_the_last_one_offered() -> None:
    page = SearchPage(query=SearchQuery(), results=(), total=PAGE_SIZE * MAX_PAGE * 3)

    assert page.pages == MAX_PAGE


async def test_snippet_highlights_the_words(async_session: AsyncSession) -> None:
    await job(async_session, description='We use Kotlin & "Ktor" for <3 years')

    [result] = (await search(async_session, text="kotlin")).results

    assert result.snippet_html == (
        "We use <mark>Kotlin</mark> &amp; &quot;Ktor&quot; for &lt;3 years"
    )


async def test_without_words_the_snippet_is_the_start_of_the_description(
    async_session: AsyncSession,
) -> None:
    description = "First words <b>bold</b>. " + "filler " * 200
    await job(async_session, description=description)

    [result] = (await search(async_session)).results

    assert result.snippet_html.startswith("First words &lt;b&gt;bold&lt;/b&gt;. filler")
    assert result.snippet_html.endswith("…")
    assert len(result.snippet_html) < len(description)


async def test_the_snippet_is_short(async_session: AsyncSession) -> None:
    await job(async_session, description=" ".join(f"word{n} kotlin" for n in range(500)))

    [result] = (await search(async_session, text="kotlin")).results

    assert result.snippet_html.count(" ") <= 30
