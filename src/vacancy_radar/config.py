"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    `database_url` has no default on purpose: a deployment with a missing
    variable must fail at startup instead of silently connecting to localhost.

    `redis_url` is optional because only Celery needs it. The collector CLI
    and Alembic run without Redis (the scheduled GitHub Actions collection
    does), and `create_celery()` fails at startup when it is missing.

    `disabled_sources` is the kill switch: a comma-separated list of source
    slugs (`DISABLED_SOURCES=dou`) that are never fetched, so a source can be
    stopped without a code change.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: PostgresDsn
    redis_url: RedisDsn | None = None
    # NoDecode: read the variable as plain text instead of JSON.
    disabled_sources: Annotated[frozenset[str], NoDecode] = frozenset()

    @field_validator("disabled_sources", mode="before")
    @classmethod
    def split_slugs(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(slug.strip() for slug in value.split(",") if slug.strip())
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
