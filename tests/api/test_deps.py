import pytest
from sqlalchemy import text

from vacancy_radar.api.deps import get_session
from vacancy_radar.db.engine import get_async_sessionmaker

pytestmark = pytest.mark.anyio


async def test_get_session_opens_a_session_on_the_configured_database(
    test_database_url: str,
) -> None:
    try:
        async for session in get_session():
            assert await session.scalar(text("SELECT current_database()")) == "vacancy_radar_test"
    finally:
        await get_async_sessionmaker().kw["bind"].dispose()
        get_async_sessionmaker.cache_clear()
