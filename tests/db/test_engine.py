import pytest
from sqlalchemy import make_url, text

from tests.support import to_sqlalchemy
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import (
    get_async_sessionmaker,
    get_sessionmaker,
    make_async_engine,
    make_engine,
)


def test_sync_engine_connects(test_database_url: str) -> None:
    engine = make_engine(to_sqlalchemy(test_database_url))
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_async_engine_connects(test_database_url: str) -> None:
    engine = make_async_engine(to_sqlalchemy(test_database_url))
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


def test_session_factories_use_database_url_from_settings() -> None:
    expected = make_url(str(get_settings().database_url))
    try:
        sync_engine = get_sessionmaker().kw["bind"]
        async_engine = get_async_sessionmaker().kw["bind"]

        assert sync_engine.url == expected
        assert async_engine.url == expected
        assert get_sessionmaker() is get_sessionmaker()
    finally:
        get_sessionmaker.cache_clear()
        get_async_sessionmaker.cache_clear()
