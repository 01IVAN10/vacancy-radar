"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Annotated, Final, Literal

from pydantic import PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# HS256 keys shorter than its 256-bit output weaken the signature.
MIN_SECRET_KEY_LENGTH: Final = 32


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
    here, by every process that reads it.

    `disabled_sources` is the kill switch: a comma-separated list of source
    slugs (`DISABLED_SOURCES=dou`) that are never fetched, so a source can be
    stopped without a code change.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: PostgresDsn
    redis_url: RedisDsn | None = None
    secret_key: SecretStr | None = None
    # NoDecode: read the variable as plain text instead of JSON.
    disabled_sources: Annotated[frozenset[str], NoDecode] = frozenset()

    @field_validator("disabled_sources", mode="before")
    @classmethod
    def split_slugs(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(slug.strip() for slug in value.split(",") if slug.strip())
        return value

    @property
    def cookie_secure(self) -> bool:
        """Send cookies over HTTPS only. Local development runs on plain HTTP."""
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
