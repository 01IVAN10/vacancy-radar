import pytest
from pydantic import ValidationError

from vacancy_radar.config import Settings, get_settings

DATABASE_URL = "postgresql+psycopg://user:secret@db.example:5432/vacancy_radar"
REDIS_URL = "redis://cache.example:6379/0"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Provide a complete, valid environment with nothing else set."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    return monkeypatch


def load() -> Settings:
    # Ignore any developer `.env` so tests see only the variables they set.
    return Settings(_env_file=None)


def test_reads_values_from_environment(env: pytest.MonkeyPatch) -> None:
    env.setenv("APP_ENV", "production")
    env.setenv("LOG_LEVEL", "WARNING")

    settings = load()

    assert str(settings.database_url) == DATABASE_URL
    assert str(settings.redis_url) == REDIS_URL
    assert settings.app_env == "production"
    assert settings.log_level == "WARNING"


def test_defaults_to_local_environment(env: pytest.MonkeyPatch) -> None:
    settings = load()

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("missing", ["DATABASE_URL", "REDIS_URL"])
def test_connection_urls_are_required(env: pytest.MonkeyPatch, missing: str) -> None:
    env.delenv(missing)

    with pytest.raises(ValidationError, match=missing.lower()):
        load()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DATABASE_URL", "mysql://user@db.example/vacancy_radar"),
        ("REDIS_URL", "http://cache.example"),
        ("APP_ENV", "staging"),
    ],
)
def test_rejects_invalid_values(env: pytest.MonkeyPatch, variable: str, value: str) -> None:
    env.setenv(variable, value)

    with pytest.raises(ValidationError, match=variable.lower()):
        load()


def test_get_settings_returns_cached_instance(env: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
