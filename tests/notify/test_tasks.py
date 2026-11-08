"""The notification tasks, run in-process with `apply()`, against the test database.

`apply()` runs retries at once, ignoring their countdown.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import redis
import respx
from celery.exceptions import Retry
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from tests.notify.factories import add_subscription, add_user, add_vacancy
from tests.support import fixture_bytes
from vacancy_radar.config import get_settings
from vacancy_radar.db.models import Notification
from vacancy_radar.notify import tasks
from vacancy_radar.notify.sending import SendResult
from vacancy_radar.notify.tasks import RETRY_CAP_SECONDS, match_task, retry_expires, send_task
from vacancy_radar.sources.ratelimit import TokenBucket
from vacancy_radar.telegram.client import API_URL
from vacancy_radar.worker import NOTIFY_EXPIRES_SECONDS

TOKEN = "7000000001:AAtest-token"


@pytest.fixture(autouse=True)
def task_environment(
    committed_database: Engine, redis_client: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    fast = TokenBucket(redis_client, rate=1000, capacity=100)
    monkeypatch.setattr(tasks, "get_rate_limiter", lambda: fast)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=f"{API_URL}/bot{TOKEN}", assert_all_called=False) as router:
        yield router


@pytest.fixture
def queued_send(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """`notify.send` as `notify.match` queues it, without a broker."""
    delay = Mock()
    monkeypatch.setattr(send_task, "delay", delay)
    return delay


def seed(engine: Engine) -> None:
    """A linked account with a subscription, and a vacancy it matches, seen just now."""
    now = datetime.now(UTC)
    with Session(engine) as session:
        user = add_user(session, linked_at=now - timedelta(days=1))
        add_subscription(session, user, active_since=now - timedelta(days=1))
        add_vacancy(session, first_seen_at=now - timedelta(minutes=10))
        session.commit()


def statuses(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.scalars(select(Notification.status).order_by(Notification.id)))


def test_match_clusters_queues_and_sends(committed_database: Engine, queued_send: Mock) -> None:
    seed(committed_database)

    result = match_task.apply()

    assert result.successful(), result.result
    assert result.result == {"clustered": 1, "queued": 1}
    assert statuses(committed_database) == ["pending"]
    queued_send.assert_called_once_with()


def test_match_with_nothing_new_sends_nothing(
    committed_database: Engine, queued_send: Mock
) -> None:
    seed(committed_database)
    match_task.apply()

    assert match_task.apply().result == {"clustered": 0, "queued": 0}
    assert queued_send.call_count == 1


def test_send_delivers_once(
    committed_database: Engine, api: respx.MockRouter, queued_send: Mock
) -> None:
    route = api.post("/sendMessage").respond(
        200, content=fixture_bytes("telegram/send_message.json")
    )
    seed(committed_database)
    match_task.apply()

    first = send_task.apply()
    second = send_task.apply()

    assert first.successful(), first.result
    assert first.result == {"sent": 1, "failed": 0, "skipped": 0, "retry_in": None}
    assert second.result == {"sent": 0, "failed": 0, "skipped": 0, "retry_in": None}
    assert route.call_count == 1
    assert statuses(committed_database) == ["sent"]


def test_send_after_429_retries_and_waits_for_retry_after(
    committed_database: Engine, api: respx.MockRouter, queued_send: Mock
) -> None:
    route = api.post("/sendMessage").respond(
        429, content=fixture_bytes("telegram/too_many_requests.json")
    )
    seed(committed_database)
    match_task.apply()

    result = send_task.apply()

    # The retry ran at once under apply(), and the row was not due yet.
    assert result.successful(), result.result
    assert route.call_count == 1
    assert statuses(committed_database) == ["pending"]


def test_send_retries_with_the_delay_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "send_pending", lambda *_, **__: SendResult(retry_in=17))
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(send_task, "retry", retry)

    send_task.apply()

    assert retry.call_args.kwargs["countdown"] == 17


def test_send_leaves_a_long_wait_to_beat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tasks, "send_pending", lambda *_, **__: SendResult(retry_in=RETRY_CAP_SECONDS + 1)
    )
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(send_task, "retry", retry)

    result = send_task.apply()

    assert result.successful()
    assert not retry.called


def test_send_without_a_token_sends_nothing(
    committed_database: Engine, api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    route = api.post("/sendMessage").respond(200)

    with capture_logs() as logs:
        result = send_task.apply()

    assert result.successful()
    assert not route.called
    assert [log["event"] for log in logs] == ["telegram.not_configured"]


def test_send_with_a_rejected_token_fails_without_retrying(
    committed_database: Engine, api: respx.MockRouter, queued_send: Mock
) -> None:
    route = api.post("/sendMessage").respond(
        401, content=fixture_bytes("telegram/unauthorized.json")
    )
    seed(committed_database)
    match_task.apply()

    with capture_logs() as logs:
        result = send_task.apply()

    assert result.failed()
    assert route.call_count == 1
    assert "telegram.token_rejected" in [log["event"] for log in logs]
    assert statuses(committed_database) == ["pending"]


def test_send_goes_through_the_rate_limiter(
    committed_database: Engine,
    api: respx.MockRouter,
    queued_send: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api.post("/sendMessage").respond(200, content=fixture_bytes("telegram/send_message.json"))
    hosts: list[str] = []
    monkeypatch.setattr(
        "vacancy_radar.sources.http.wait_for_token", lambda _bucket, host: hosts.append(host)
    )
    seed(committed_database)
    match_task.apply()

    send_task.apply()

    assert hosts == ["api.telegram.org"]


def test_retry_expires_after_its_countdown() -> None:
    before = datetime.now(UTC)

    expires = retry_expires(60)

    assert before + timedelta(seconds=60 + NOTIFY_EXPIRES_SECONDS) <= expires
    assert expires <= datetime.now(UTC) + timedelta(seconds=60 + NOTIFY_EXPIRES_SECONDS)
