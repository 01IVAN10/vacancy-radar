import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import add_vacancy
from vacancy_radar.db.sources import SourceSite, source_sites

pytestmark = pytest.mark.anyio


async def test_every_source_by_slug_with_its_name_and_site(async_session: AsyncSession) -> None:
    await async_session.run_sync(lambda sync: add_vacancy(sync, source="dou"))
    await async_session.run_sync(lambda sync: add_vacancy(sync, source="djinni"))

    assert await source_sites(async_session) == {
        "djinni": SourceSite("Djinni", "https://djinni.co"),
        "dou": SourceSite("DOU", "https://jobs.dou.ua"),
    }
