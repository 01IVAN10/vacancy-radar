"""The reprocessing task, run in-process with `apply()`, against the test database."""

import pytest
import redis
import respx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.collect.service import ensure_source, upsert_raw_items
from vacancy_radar.normalize import tasks
from vacancy_radar.normalize.tasks import normalize_pending_task
from vacancy_radar.ratelimit import TokenBucket
from vacancy_radar.sources import ADAPTERS, dou


@pytest.fixture(autouse=True)
def task_environment(
    committed_database: Engine, redis_client: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    fast = TokenBucket(redis_client, rate=1000, capacity=100)
    monkeypatch.setattr(tasks, "get_rate_limiter", lambda: fast)
    with Session(committed_database) as session:
        upsert_raw_items(
            session,
            ensure_source(session, ADAPTERS["dou"]),
            dou.parse_feed(fixture_bytes("dou/rss_all.xml")).items,
        )
        session.commit()


@respx.mock
def test_task_normalizes_pending_items() -> None:
    result = normalize_pending_task.apply()

    assert result.successful(), result.result
    # DOU salaries are in USD, so NBU is never called.
    assert result.result == {"normalized": 6, "with_salary": 2, "converted": 0}
    assert normalize_pending_task.apply().result == {
        "normalized": 0,
        "with_salary": 0,
        "converted": 0,
    }
