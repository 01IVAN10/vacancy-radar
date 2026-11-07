"""Engine and session factories.

The API uses the async engine and Celery workers use the sync one. Both run on
psycopg 3, so they accept the same `postgresql+psycopg://` URL.
"""

from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from vacancy_radar.config import get_settings


def make_engine(url: str, **kwargs: Any) -> Engine:
    """Create a sync engine. `pool_pre_ping` drops connections the server closed."""
    return create_engine(url, pool_pre_ping=True, **kwargs)


def make_async_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """Create an async engine for the same database."""
    return sa_create_async_engine(url, pool_pre_ping=True, **kwargs)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    """Process-wide sync session factory, built on first use from settings."""
    return sessionmaker(make_engine(str(get_settings().database_url)))


@lru_cache
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Process-wide async session factory, built on first use from settings.

    `expire_on_commit=False`: after a commit, attributes stay loaded instead of
    being reloaded on access, which an async session cannot do implicitly.
    """
    return async_sessionmaker(
        make_async_engine(str(get_settings().database_url)), expire_on_commit=False
    )
