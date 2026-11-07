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
    monkeypatch.delenv("DISABLED_SOURCES", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
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


def test_database_url_is_required(env: pytest.MonkeyPatch) -> None:
    env.delenv("DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        load()


def test_redis_url_is_optional(env: pytest.MonkeyPatch) -> None:
    # The collector CLI and Alembic run without Redis; only Celery needs it.
    env.delenv("REDIS_URL")

    assert load().redis_url is None


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


def test_no_source_is_disabled_by_default(env: pytest.MonkeyPatch) -> None:
    assert load().disabled_sources == frozenset()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("dou", {"dou"}),
        (" dou , djinni ", {"dou", "djinni"}),
        # An unset GitHub repository variable arrives as an empty string.
        ("", set()),
        (",", set()),
    ],
)
def test_disabled_sources_is_a_comma_separated_list(
    env: pytest.MonkeyPatch, value: str, expected: set[str]
) -> None:
    env.setenv("DISABLED_SOURCES", value)

    assert load().disabled_sources == expected


def test_secret_key_is_optional_and_hidden(env: pytest.MonkeyPatch) -> None:
    # Only the web app signs tokens; the collector and Alembic run without a key.
    assert load().secret_key is None

    env.setenv("SECRET_KEY", "s" * 40)
    settings = load()

    assert settings.secret_key is not None
    assert settings.secret_key.get_secret_value() == "s" * 40
    assert "s" * 40 not in repr(settings)


@pytest.mark.parametrize(
    ("app_env", "secure"), [("local", False), ("test", False), ("production", True)]
)
def test_cookies_are_secure_in_production_only(
    env: pytest.MonkeyPatch, app_env: str, secure: bool
) -> None:
    env.setenv("APP_ENV", app_env)

    assert load().cookie_secure is secure


@pytest.mark.parametrize("value", ["", "   "])
def test_telegram_is_optional(env: pytest.MonkeyPatch, value: str) -> None:
    # `.env.example` ships `TELEGRAM_BOT_TOKEN=` empty; that means no bot.
    env.setenv("TELEGRAM_BOT_TOKEN", value)
    env.setenv("TELEGRAM_BOT_USERNAME", value)

    settings = load()

    assert (settings.telegram_bot_token, settings.telegram_bot_username) == (None, None)


def test_telegram_bot_token_is_hidden(env: pytest.MonkeyPatch) -> None:
    token = "7000000001:AAsecret-token-value"
    env.setenv("TELEGRAM_BOT_TOKEN", token)
    settings = load()

    assert settings.telegram_bot_token is not None
    assert settings.telegram_bot_token.get_secret_value() == token
    assert token not in repr(settings)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("vacancy_radar_bot", "vacancy_radar_bot"),
        ("@VacancyRadarBot", "VacancyRadarBot"),  # as BotFather shows it
        (" vacancy_radar_bot ", "vacancy_radar_bot"),
    ],
)
def test_telegram_bot_username(env: pytest.MonkeyPatch, value: str, expected: str) -> None:
    env.setenv("TELEGRAM_BOT_USERNAME", value)

    assert load().telegram_bot_username == expected


@pytest.mark.parametrize(
    "value",
    [
        "vacancy_radar",  # does not end in "bot"
        "abot",  # too short
        "a" * 30 + "bot",  # too long
        "radar-bot",  # a character usernames cannot have
        "1radar_bot",  # starts with a digit
        "radar_bot?start=x",  # would break the deep link
    ],
)
def test_telegram_bot_username_rejects_what_telegram_does(
    env: pytest.MonkeyPatch, value: str
) -> None:
    env.setenv("TELEGRAM_BOT_USERNAME", value)

    with pytest.raises(ValidationError, match="bot username"):
        load()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "http://localhost:8000"),
        ("https://radar.example/", "https://radar.example"),
        ("https://radar.example/app", "https://radar.example/app"),
    ],
)
def test_site_url_has_no_trailing_slash(
    env: pytest.MonkeyPatch, value: str | None, expected: str
) -> None:
    if value is not None:
        env.setenv("PUBLIC_BASE_URL", value)

    assert load().site_url == expected
