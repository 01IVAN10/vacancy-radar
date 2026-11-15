"""Application settings loaded from environment variables and `.env`."""

import re
from functools import lru_cache
from typing import Annotated, Final, Literal

from pydantic import AnyHttpUrl, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# HS256 keys shorter than its 256-bit output weaken the signature.
MIN_SECRET_KEY_LENGTH: Final = 32
# Both committed development keys (`compose.yaml`, `.env.example`) contain it.
DEVELOPMENT_KEY_MARKER: Final = "local-development-only"

# Telegram bot usernames: 5 to 32 letters, digits and underscores, ending in "bot".
BOT_USERNAME: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,28}[Bb][Oo][Tt]", re.ASCII)


class Settings(BaseSettings):
    """Runtime configuration.

    `database_url` has no default on purpose: a deployment with a missing
    variable must fail at startup instead of silently connecting to localhost.

    `redis_url` is optional because only Celery needs it. The collector CLI
    and Alembic run without Redis (the scheduled GitHub Actions collection
    does), and `create_celery()` fails at startup when it is missing.

    `secret_key` signs session tokens. Like `redis_url` it is optional, so the
    collector and Alembic run without it, and `create_app()` fails at startup
    when it is missing. A key shorter than `MIN_SECRET_KEY_LENGTH` is rejected
    here, by every process that reads it. Validation errors never repeat the
    input, so neither the key nor a password in `database_url` reaches a log.

    `disabled_sources` is the kill switch: a comma-separated list of source
    slugs (`DISABLED_SOURCES=dou`) that are never fetched, so a source can be
    stopped without a code change.

    `telegram_bot_token` lets the bot process and the worker call the Bot
    API; without it the bot does not start. `telegram_bot_username` is all
    the web app needs, to build the deep link that links an account; without
    it the Telegram page says linking is not set up. `public_base_url` is
    where the site is reached, for links in the bot's replies. Its scheme
    decides whether cookies are `Secure`.

    Rules that apply to production only are in `check_production()`, not
    here: the scheduled collection runs with `APP_ENV=production` and only
    `DATABASE_URL`, and must keep working.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: PostgresDsn
    redis_url: RedisDsn | None = None
    secret_key: SecretStr | None = None
    # NoDecode: read the variable as plain text instead of JSON.
    disabled_sources: Annotated[frozenset[str], NoDecode] = frozenset()
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")

    @field_validator("disabled_sources", mode="before")
    @classmethod
    def split_slugs(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(slug.strip() for slug in value.split(",") if slug.strip())
        return value

    @field_validator("secret_key")
    @classmethod
    def check_secret_key_length(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters")
        return value

    @field_validator("telegram_bot_token", mode="before")
    @classmethod
    def empty_token_is_none(cls, value: object) -> object:
        """`TELEGRAM_BOT_TOKEN=` as copied from `.env.example` means no bot, not an empty token."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def check_bot_username(cls, value: object) -> object:
        """Accept the name with or without the leading `@` BotFather shows."""
        if not isinstance(value, str):
            return value
        name = value.strip().removeprefix("@")
        if not name:
            return None
        if not BOT_USERNAME.fullmatch(name):
            raise ValueError("a bot username has 5 to 32 letters, digits or _ and ends in 'bot'")
        return name

    @property
    def site_url(self) -> str:
        """`public_base_url` without a trailing slash, ready to append a path to."""
        return str(self.public_base_url).rstrip("/")

    @property
    def cookie_secure(self) -> bool:
        """Send cookies over HTTPS only when the site is served over HTTPS.

        Local development runs on plain HTTP, where a `Secure` cookie would
        never be sent back. Production must use https: `check_production()`.
        """
        return self.public_base_url.scheme == "https"


class ProductionSettingsError(RuntimeError):
    """A production process was started with settings it must not run with."""


def check_production(settings: Settings) -> None:
    """Refuse to run in production with settings meant for local development.

    Called by the long-running processes (web app, Celery, bot), not by
    `Settings` itself, so the collector CLI runs in production without them.
    Every problem is named in one error, so one restart is enough to fix them.
    """
    if settings.app_env != "production":
        return
    problems = []
    if settings.public_base_url.scheme != "https":
        problems.append(f"PUBLIC_BASE_URL must use https, got {settings.site_url}")
    if (
        settings.secret_key is not None
        and DEVELOPMENT_KEY_MARKER in settings.secret_key.get_secret_value()
    ):
        problems.append("SECRET_KEY is a local development key")
    if problems:
        raise ProductionSettingsError("invalid production settings: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
