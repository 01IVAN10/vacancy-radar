"""The Celery task, run in-process with `apply()`, against the test database and Redis.

`apply()` runs retries at once, ignoring their countdown, so a whole retry
sequence runs inside one test.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import redis
import respx
from celery.result import EagerResult
from sqlalchemy import Engine, func, select
from structlog.testing import capture_logs

from tests.support import fixture_bytes, redis_test_url
from vacancy_radar.collect import tasks
from vacancy_radar.collect.tasks import fetch_source, retry_expires
from vacancy_radar.config import get_settings
from vacancy_radar.db.models import CollectRun, RawVacancy
from vacancy_radar.sources.djinni import FEED_URL as DJINNI_FEED_URL
from vacancy_radar.sources.djinni import DjinniAdapter
from vacancy_radar.sources.dou import FEED_URL as DOU_FEED_URL
from vacancy_radar.sources.ratelimit import KEY_PREFIX, TokenBucket
from vacancy_radar.worker import COLLECT_EXPIRES_SECONDS

DJINNI_FEED = fixture_bytes("djinni/rss_all.xml")
# Kept before `task_environment` replaces it with a fast limiter.
REAL_GET_RATE_LIMITER = tasks.get_rate_limiter


@pytest.fixture(autouse=True)
def task_environment(
    committed_database: Engine, redis_client: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Retries run back to back under apply(), so a 1 request/s limit would make
    # each test sleep. Rate limiting itself is tested in tests/sources.
    fast = TokenBucket(redis_client, rate=1000, capacity=100)
    monkeypatch.setattr(tasks, "get_rate_limiter", lambda: fast)


def run(slug: str) -> EagerResult[dict[str, object]]:
    return fetch_source.apply(args=(slug,))


def run_ok(slug: str) -> dict[str, object]:
    """Run the task, check it succeeded, and return its summary."""
    result = run(slug)
    assert result.successful(), result.result
    assert isinstance(result.result, dict)
    return result.result


def statuses(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.scalars(select(CollectRun.status).order_by(CollectRun.id)))


def stored(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(RawVacancy)) or 0


def test_collects_a_due_source(respx_mock: respx.MockRouter, committed_database: Engine) -> None:
    respx_mock.get(DJINNI_FEED_URL).respond(200, content=DJINNI_FEED)

    summary = run_ok("djinni")

    assert summary["event"] == "collect.finished"
    assert summary["inserted"] == 5
    assert stored(committed_database) == 5
    assert statuses(committed_database) == ["ok"]


def test_second_run_inside_min_interval_sends_no_request(
    respx_mock: respx.MockRouter, committed_database: Engine
) -> None:
    route = respx_mock.get(DJINNI_FEED_URL).respond(200, content=DJINNI_FEED)

    run_ok("djinni")
    second = run_ok("djinni")

    assert route.call_count == 1
    assert (second["event"], second["reason"]) == ("collect.skipped", "min_interval")
    assert statuses(committed_database) == ["ok"]


def test_running_twice_stores_each_vacancy_once(
    respx_mock: respx.MockRouter, committed_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DjinniAdapter, "min_interval", timedelta(0))
    respx_mock.get(DJINNI_FEED_URL).respond(200, content=DJINNI_FEED)

    run_ok("djinni")
    second = run_ok("djinni")

    assert (second["inserted"], second["unchanged"]) == (0, 5)
    assert stored(committed_database) == 5


def test_transient_failures_are_retried_until_one_succeeds(
    respx_mock: respx.MockRouter, committed_database: Engine
) -> None:
    route = respx_mock.get(DJINNI_FEED_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ReadTimeout("timed out"),
            httpx.Response(200, content=DJINNI_FEED),
        ]
    )

    run_ok("djinni")

    assert route.call_count == 3
    # Each attempt is a request, so each is recorded; the retries were not
    # held back by the failed runs before them.
    assert statuses(committed_database) == ["failed", "failed", "ok"]
    assert stored(committed_database) == 5


def test_retry_delivered_again_sends_no_request(
    respx_mock: respx.MockRouter, committed_database: Engine
) -> None:
    route = respx_mock.get(DJINNI_FEED_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=DJINNI_FEED)]
    )
    run_ok("djinni")
    with committed_database.connect() as connection:
        failed_run = connection.scalar(select(func.min(CollectRun.id)))

    # The broker delivers the retry of the first, failed run a second time.
    again = fetch_source.apply(args=("djinni",), kwargs={"retry_of": failed_run})

    assert again.successful(), again.result
    assert isinstance(again.result, dict)
    assert (again.result["event"], again.result["reason"]) == ("collect.skipped", "superseded")
    assert route.call_count == 2
    assert statuses(committed_database) == ["failed", "ok"]


def test_gives_up_after_max_retries(
    respx_mock: respx.MockRouter, committed_database: Engine
) -> None:
    route = respx_mock.get(DJINNI_FEED_URL).respond(503)

    with capture_logs() as logs:
        result = run("djinni")

    assert result.failed()
    assert isinstance(result.result, httpx.HTTPError)
    assert route.call_count == 1 + DjinniAdapter.max_retries
    assert statuses(committed_database) == ["failed"] * 4
    failures = [entry for entry in logs if entry["event"] == "collect.failed"]
    assert [entry["retries"] for entry in failures] == [0, 1, 2, 3]
    assert failures[-1]["retry_in"] is None


def test_dou_is_never_retried(respx_mock: respx.MockRouter, committed_database: Engine) -> None:
    # DOU's consent counts retries as requests: one request per 30 minutes.
    route = respx_mock.get(DOU_FEED_URL).respond(503)

    result = run("dou")

    assert result.failed()
    assert route.call_count == 1
    assert statuses(committed_database) == ["failed"]


@pytest.mark.parametrize("status", [403, 429])
def test_blocked_request_is_not_retried_and_logged_as_blocked(
    respx_mock: respx.MockRouter, committed_database: Engine, status: int
) -> None:
    route = respx_mock.get(DJINNI_FEED_URL).respond(status)

    with capture_logs() as logs:
        result = run("djinni")

    assert result.failed()
    assert route.call_count == 1
    [entry] = [entry for entry in logs if entry["log_level"] == "error"]
    assert (entry["event"], entry["source"]) == ("collect.blocked", "djinni")


def test_disabled_source_sends_no_request(
    respx_mock: respx.MockRouter, committed_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISABLED_SOURCES", "dou")
    get_settings.cache_clear()
    route = respx_mock.get(DOU_FEED_URL).respond(200)

    summary = run_ok("dou")

    assert (summary["event"], summary["reason"]) == ("collect.skipped", "disabled")
    assert route.call_count == 0
    assert statuses(committed_database) == []


def test_requests_go_through_the_shared_rate_limiter(
    respx_mock: respx.MockRouter, redis_client: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_rate_limiter", REAL_GET_RATE_LIMITER)
    monkeypatch.setenv("REDIS_URL", redis_test_url())
    get_settings.cache_clear()
    REAL_GET_RATE_LIMITER.cache_clear()
    respx_mock.get(DJINNI_FEED_URL).respond(200, content=DJINNI_FEED)

    try:
        run_ok("djinni")
    finally:
        REAL_GET_RATE_LIMITER().redis.close()
        REAL_GET_RATE_LIMITER.cache_clear()

    assert redis_client.exists(KEY_PREFIX + "djinni.co")


def test_retry_expires_after_its_countdown() -> None:
    before = datetime.now(UTC)

    expires = retry_expires(60)

    assert expires >= before + timedelta(seconds=60 + COLLECT_EXPIRES_SECONDS)
    assert expires <= datetime.now(UTC) + timedelta(seconds=60 + COLLECT_EXPIRES_SECONDS)


def test_rate_limiter_requires_redis_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("REDIS_URL")
    monkeypatch.chdir(tmp_path)  # no developer `.env` to supply REDIS_URL
    get_settings.cache_clear()
    REAL_GET_RATE_LIMITER.cache_clear()

    with pytest.raises(RuntimeError, match="REDIS_URL is required"):
        REAL_GET_RATE_LIMITER()
