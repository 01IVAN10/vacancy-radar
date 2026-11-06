from pathlib import Path

import pytest
import structlog

from vacancy_radar.collect.tasks import FETCH_SOURCE, fetch_source
from vacancy_radar.config import get_settings
from vacancy_radar.normalize.tasks import NORMALIZE_PENDING
from vacancy_radar.sources import ADAPTERS
from vacancy_radar.worker import (
    COLLECT_EVERY,
    NORMALIZE_EVERY,
    celery_app,
    create_celery,
    ping,
    setup_structlog,
)


def test_ping_returns_pong() -> None:
    # apply() runs the task in-process, so no broker is needed.
    assert ping.apply().get() == "pong"


def test_beat_offers_every_source_to_fetch_source() -> None:
    schedule = celery_app.conf.beat_schedule

    assert {name for name in schedule if name.startswith("fetch-")} == {
        f"fetch-{slug}" for slug in ADAPTERS
    }
    for slug in ADAPTERS:
        entry = schedule[f"fetch-{slug}"]
        assert entry["task"] == FETCH_SOURCE
        assert entry["args"] == (slug,)
        assert entry["schedule"] == COLLECT_EVERY


def test_beat_runs_more_often_than_any_source_may_be_fetched() -> None:
    # min_interval, not Beat, decides when a request is sent.
    assert all(adapter.min_interval > COLLECT_EVERY for adapter in ADAPTERS.values())


def test_beat_sends_the_reprocessing_task() -> None:
    entry = celery_app.conf.beat_schedule["normalize-pending"]

    assert entry["task"] == NORMALIZE_PENDING
    assert entry["schedule"] == NORMALIZE_EVERY
    assert celery_app.tasks[NORMALIZE_PENDING].name == NORMALIZE_PENDING


def test_unpicked_beat_messages_expire_before_the_next_one_is_sent() -> None:
    for entry in celery_app.conf.beat_schedule.values():
        assert entry["options"]["expires"] < entry["schedule"].total_seconds()


def test_collection_task_is_registered() -> None:
    assert celery_app.tasks[FETCH_SOURCE].name == fetch_source.name == FETCH_SOURCE


def test_worker_logs_through_structlog() -> None:
    try:
        setup_structlog()
        assert structlog.is_configured()
    finally:
        structlog.reset_defaults()


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
