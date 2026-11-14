import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import add_clustered_vacancy
from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.api.deps import current_time
from vacancy_radar.domain.analytics import MIN_SAMPLE

pytestmark = pytest.mark.anyio

# Thursday 12 November; the page is asked on Friday 20 November in Kyiv, when the week
# of 9 November is over and November is not.
THURSDAY = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SALARY: dict[str, Any] = {"salary_currency": "USD", "salary_period": "month"}


@pytest.fixture(autouse=True)
def fixed_time(app: FastAPI) -> None:
    app.dependency_overrides[current_time] = lambda: NOW


async def jobs(session: AsyncSession, count: int, **fields: Any) -> None:
    def add(sync: Any) -> None:
        for _ in range(count):
            add_clustered_vacancy(sync, **({"first_seen_at": THURSDAY} | fields))

    await session.run_sync(add)


async def salaries(session: AsyncSession, count: int, usd: int, **fields: Any) -> None:
    await jobs(session, count, salary_min=usd, salary_usd_min=usd, **SALARY | fields)


async def page(client: AsyncClient, session: AsyncSession, **params: str) -> str:
    await session.run_sync(refresh_views)
    response = await client.get("/analytics", params=params)
    assert response.status_code == 200
    return response.text


def chart_data(html: str) -> Any:
    found = re.search(r'<script type="application/json" id="analytics-data">(.*?)</script>', html)
    assert found is not None
    return json.loads(found[1])


async def test_says_so_before_any_job_is_counted(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    html = await page(client, async_session)

    assert "No jobs are counted yet" in html
    assert "analytics-data" not in html
    assert "chart.js" not in html


async def test_shows_the_three_aggregates_with_their_sample_sizes(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    await jobs(async_session, 3, skills=["python", "sql"])
    await jobs(async_session, 1, skills=["go"], source="dou")

    html = await page(client, async_session)

    # Words as a reader sees them: entities decoded, line breaks of the template collapsed.
    text = " ".join(unescape(html).split())
    for shown in [
        "<strong>4</strong> jobs (4 postings) since 12 Nov 2026",
        "Top skills, November 2026*",
        "3 of 4",
        "75.0%",
        "Salaries by week, Every level",
        "New jobs by week",
    ]:
        assert shown in text
    data = chart_data(html)
    assert data["skills"] == {
        "jobs": 4,
        "labels": ["Python", "SQL", "Go"],
        "shares": [75.0, 75.0, 25.0],
        "n": [3, 3, 1],
    }
    # Collection started on Thursday 12 November here, so its week is partial.
    assert data["jobs"] == {
        "labels": ["9 Nov*"],
        "series": [
            # A series is coloured by its key, so a new source cannot repaint the others.
            {"key": "total", "name": "All sources", "values": [4]},
            {"key": "djinni", "name": "Djinni", "values": [3]},
            {"key": "dou", "name": "DOU", "values": [1]},
        ],
    }


async def test_names_and_links_the_sources(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    # DOU's consent asks aggregated statistics to name DOU as a source, with a link.
    await jobs(async_session, 1, source="dou")
    await jobs(async_session, 1, source="djinni")

    html = await page(client, async_session)

    assert '<a href="https://jobs.dou.ua" rel="noopener">DOU</a>' in html
    assert '<a href="https://djinni.co" rel="noopener">Djinni</a>' in html


async def test_a_hidden_median_is_not_in_the_page_at_all(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    await salaries(async_session, MIN_SAMPLE - 1, 4321)

    html = await page(client, async_session)

    assert "4321" not in html
    assert "4,321" not in html
    assert "hidden: fewer than 10 salaries" in html
    assert chart_data(html)["salaries"] == {
        "labels": ["9 Nov*"],
        "n": [MIN_SAMPLE - 1],
        "p25": [None],
        "median": [None],
        "p75": [None],
    }


async def test_a_median_of_enough_salaries_is_shown(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    await salaries(async_session, MIN_SAMPLE, 4321)

    html = await page(client, async_session)

    assert "$4,321" in html
    assert chart_data(html)["salaries"]["median"] == [4321]


async def test_salaries_of_one_level(client: AsyncClient, async_session: AsyncSession) -> None:
    await salaries(async_session, MIN_SAMPLE, 5000, seniority=["senior"])
    await salaries(async_session, MIN_SAMPLE, 2000, seniority=["junior"])

    html = await page(client, async_session, level="senior")

    assert "Salaries by week, Senior" in html
    assert '<option value="senior" selected>' in html
    assert chart_data(html)["salaries"]["median"] == [5000]


async def test_top_skills_of_a_chosen_month(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    await jobs(async_session, 1, skills=["go"], first_seen_at=datetime(2026, 10, 20, tzinfo=UTC))
    await jobs(async_session, 1, skills=["python"])

    html = await page(client, async_session, month="2026-10")

    assert "Top skills, October 2026*" in html
    assert '<option value="2026-10" selected>' in html
    assert chart_data(html)["skills"]["labels"] == ["Go"]


async def test_a_month_without_data(client: AsyncClient, async_session: AsyncSession) -> None:
    await jobs(async_session, 1)

    html = await page(client, async_session, month="2025-01")

    # The heading and the form name the same month, so sending the form keeps it.
    assert "Top skills, January 2025" in html
    assert re.findall(r'<option value="(\d{4}-\d{2})"', html) == ["2026-11", "2025-01"]
    assert '<option value="2025-01" selected>January 2025</option>' in html
    assert "No jobs with skills were first seen in January 2025." in html
    assert chart_data(html)["skills"]["labels"] == []


async def test_before_any_job_names_a_skill(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    await jobs(async_session, 1, skills=[])

    html = await page(client, async_session)

    assert "<h2>Top skills</h2>" in html
    assert "No job names a known skill yet." in html
    assert 'name="month"' not in html
    assert "Top skills," not in html


async def test_marks_periods_that_are_partial_or_not_final(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    # Collection starts on Thursday 5 November, so its week is partial.
    await jobs(async_session, 1, first_seen_at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC))
    await jobs(async_session, 1)

    html = await page(client, async_session)

    assert chart_data(html)["jobs"]["labels"] == ["2 Nov*", "9 Nov"]
    text = " ".join(html.split())
    assert "From the week of 2 Nov 2026 on, the jobs that name a skill are not final." in text
    assert "Not final: postings that join a job later may still add skills." in text


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"month": "2026-13"}, "Choose a month such as 2026-11."),
        ({"level": "all"}, "Choose one of the offered levels."),
    ],
)
async def test_invalid_choices_are_refused(
    client: AsyncClient, async_session: AsyncSession, params: dict[str, str], message: str
) -> None:
    await jobs(async_session, 1)
    await async_session.run_sync(refresh_views)

    response = await client.get("/analytics", params=params)

    assert response.status_code == 422
    assert message in response.text
    assert "analytics-data" not in response.text


async def test_a_value_cannot_close_the_data_script(
    client: AsyncClient, async_session: AsyncSession
) -> None:
    # A skill no longer in the dictionary is named by its slug, which the page does not control.
    await jobs(async_session, 1, skills=["</script><img src=x>"])

    html = await page(client, async_session)

    assert "</script><img" not in html
    assert chart_data(html)["skills"]["labels"] == ["</script><img src=x>"]


async def test_serves_the_chart_script(client: AsyncClient) -> None:
    response = await client.get("/static/analytics.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "analytics-data" in response.text
