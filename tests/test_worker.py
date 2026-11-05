from pathlib import Path

import pytest

from vacancy_radar.config import get_settings
from vacancy_radar.worker import celery_app, create_celery, ping


def test_ping_returns_pong() -> None:
    # apply() runs the task in-process, so no broker is needed.
    assert ping.apply().get() == "pong"


def test_beat_schedule_is_empty_until_real_tasks_exist() -> None:
    assert celery_app.conf.beat_schedule == {}


def test_create_celery_requires_redis_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("REDIS_URL")
    # Run where no developer `.env` can supply REDIS_URL.
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="REDIS_URL is required"):
            create_celery()
    finally:
        get_settings.cache_clear()
