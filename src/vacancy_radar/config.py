"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    `database_url` has no default on purpose: a deployment with a missing
    variable must fail at startup instead of silently connecting to localhost.

    `redis_url` is optional because only Celery needs it. The collector CLI
    and Alembic run without Redis (the scheduled GitHub Actions collection
    does), and `create_celery()` fails at startup when it is missing.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: PostgresDsn
    redis_url: RedisDsn | None = None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
