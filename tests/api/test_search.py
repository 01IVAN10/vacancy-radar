from datetime import timedelta
from html import unescape
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import NOW, add_clustered_vacancy, add_vacancy
from vacancy_radar.db.models import Vacancy
from vacancy_radar.domain.search import PAGE_SIZE

pytestmark = pytest.mark.anyio

HTMX = {"HX-Request": "true"}


async def job(session: AsyncSession, **fields: Any) -> Vacancy:
    vacancy = await session.run_sync(lambda sync: add_clustered_vacancy(sync, **fields))
    await session.commit()
    return vacancy


async def test_lists_jobs_to_anyone(client: AsyncClient, async_session: AsyncSession) -> None:
    await job(
        async_session,
        title="Senior Python Developer",
        company="Acme",
        cities=["Kyiv"],
        seniority=["senior"],
        work_formats=["remote"],
        skills=["python", "django"],
        salary_min=4000,
        salary_max=5500,
        salary_currency="USD",
        salary_period="month",
        first_seen_at=NOW,
    )

    response = await client.get("/vacancies")

    assert response.status_code == 200
    page = unescape(response.text)
    assert "<html" in page
    for shown in [
        "Senior Python Developer",
        "Acme, Kyiv",
        "$4000-5500 a month",
        "Senior · Remote",
        "Python, Django",
        "First seen 8 Nov 2026",
        "1 job found",
    ]:
        assert shown in page


async def test_links_every_posting_of_a_job_with_its_source(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    first = await job(async_session, title="Data Engineer", source="djinni")
    second = await async_session.run_sync(
        lambda sync: add_vacancy(
            sync, title="Data Engineer", source="dou", cluster_id=first.cluster_id
        )
    )
    await async_session.commit()

    response = await client.get("/vacancies", params={"q": "data engineer"})

    assert f'<a href="{first.url}" rel="noopener nofollow">Djinni</a>' in response.text
    assert f'<a href="{second.url}" rel="noopener nofollow">DOU</a>' in response.text
    assert response.text.count('<article class="job">') == 1


async def test_htmx_gets_only_the_results(client: AsyncClient, async_session: AsyncSession) -> None:
    await job(async_session, title="Go Developer")

    response = await client.get("/vacancies", params={"q": "go"}, headers=HTMX)

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "Go Developer" in response.text
    assert response.headers["Vary"] == "HX-Request"


async def test_going_back_loads_the_whole_page_with_its_form(client: AsyncClient) -> None:
    response = await client.get(
        "/vacancies",
        params={"q": "python", "seniority": "senior"},
        headers=HTMX | {"HX-History-Restore-Request": "true"},
    )

    assert "<html" in response.text
    # No snapshot: a snapshot would restore old results under a reset form.
    assert 'hx-history="false"' in response.text
    assert 'value="python"' in response.text
    assert 'value="senior"\n             checked' in response.text


async def test_an_invalid_form_is_shown_again_with_its_errors(client: AsyncClient) -> None:
    response = await client.get(
        "/vacancies", params={"q": "python", "min_salary_usd": "a lot", "period": "2"}
    )

    assert response.status_code == 422
    assert "Enter a whole number of dollars" in response.text
    assert "Choose one of the offered periods." in response.text
    assert 'value="python"' in response.text
    assert 'value="a lot"' in response.text


async def test_htmx_gets_the_errors_in_place_of_the_results(client: AsyncClient) -> None:
    response = await client.get("/vacancies", params={"sort": "salary"}, headers=HTMX)

    assert response.status_code == 422
    assert "<html" not in response.text
    assert "Sort by relevance or by date." in response.text


async def test_shows_an_escaped_excerpt_never_the_whole_description(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    description = "<script>alert(1)</script> Kotlin " + "and more words " * 100
    await job(async_session, title="Android Developer", description=description)

    plain = await client.get("/vacancies")
    found = await client.get("/vacancies", params={"q": "kotlin"})

    assert "&lt;script&gt;alert(1)&lt;/script&gt; Kotlin" in plain.text
    assert "<mark>Kotlin</mark>" in found.text
    for response in (plain, found):
        assert "<script>alert(1)</script>" not in response.text
        assert description not in unescape(response.text)


async def test_only_web_links_are_linked(client: AsyncClient, async_session: AsyncSession) -> None:
    vacancy = await job(async_session, title="Odd link")
    await async_session.execute(
        update(Vacancy).where(Vacancy.id == vacancy.id).values(url="javascript:alert(1)")
    )
    await async_session.commit()

    response = await client.get("/vacancies")

    assert "Odd link" in response.text
    assert "javascript:" not in response.text


async def test_pages_keep_the_search(client: AsyncClient, async_session: AsyncSession) -> None:
    for hours in range(PAGE_SIZE + 1):
        await job(
            async_session, title=f"Python {hours}", first_seen_at=NOW - timedelta(hours=hours)
        )

    first = await client.get("/vacancies", params={"q": "python", "sort": "newest"})
    second = await client.get("/vacancies", params={"q": "python", "sort": "newest", "page": "2"})

    assert "page 1 of 2" in first.text
    assert "/vacancies?q=python&amp;sort=newest&amp;page=2" in first.text
    assert "← Previous" not in first.text
    assert "Python 20" in second.text
    assert "/vacancies?q=python&amp;sort=newest&amp;page=1" in second.text
    assert "Next →" not in second.text


async def test_says_when_nothing_is_found(client: AsyncClient) -> None:
    response = await client.get("/vacancies", params={"q": "cobol"})

    assert "No jobs found." in response.text


async def test_a_page_past_the_last_says_so(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    await job(async_session)

    response = await client.get("/vacancies", params={"page": "3"})

    assert "There are no more results." in response.text


async def test_filters_open_when_used(client: AsyncClient) -> None:
    closed = await client.get("/vacancies")
    used = await client.get("/vacancies", params={"seniority": "senior"})

    assert "<details open>" not in closed.text
    assert "<details open>" in used.text
    assert 'value="senior"\n             checked' in used.text


async def test_every_page_links_to_the_search(client: AsyncClient) -> None:
    response = await client.get("/")

    assert '<a href="/vacancies">Vacancies</a>' in response.text
    assert '<form method="get" action="/vacancies">\n  <fieldset role="search">' in response.text


async def test_the_salary_field_takes_only_digits_before_sending(client: AsyncClient) -> None:
    response = await client.get("/vacancies")

    assert 'name="min_salary_usd"' in response.text
    assert 'pattern="[0-9][0-9, ]*"' in response.text
    assert " novalidate" not in response.text
