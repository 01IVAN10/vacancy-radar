from vacancy_radar.api.app import create_app
from vacancy_radar.db.engine import get_async_sessionmaker


def test_app_uses_the_process_wide_sessions_by_default() -> None:
    try:
        assert create_app().state.config.sessions is get_async_sessionmaker()
    finally:
        get_async_sessionmaker.cache_clear()
