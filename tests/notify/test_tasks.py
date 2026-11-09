"""The notification tasks, run in-process with `apply()`, against the test database."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import redis
import respx
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from tests.notify.factories import JOB_TEXT, add_subscription, add_user, add_vacancy
from tests.support import fixture_bytes
from vacancy_radar.config import get_settings
from vacancy_radar.db import locks
from vacancy_radar.db.models import Delivery
from vacancy_radar.notify import tasks
from vacancy_radar.notify.sending import sender_lock
from vacancy_radar.notify.tasks import match_task, send_task
from vacancy_radar.ratelimit import TokenBucket
from vacancy_radar.telegram.client import API_URL

TOKEN = "7000000001:AAtest-token"
NOTHING_SENT = {"sent": 0, "failed": 0, "skipped": 0}


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
        return list(connection.scalars(select(Delivery.status).order_by(Delivery.id)))


def test_match_clusters_and_queues(committed_database: Engine) -> None:
    seed(committed_database)

    first = match_task.apply()
    second = match_task.apply()

    assert first.successful(), first.result
    assert first.result == {"opened": 1, "joined": 0, "matched": 1, "queued": 1}
    assert second.result == {"opened": 0, "joined": 0, "matched": 0, "queued": 0}
    assert statuses(committed_database) == ["pending"]


def test_a_duplicate_of_a_matched_vacancy_queues_nothing(committed_database: Engine) -> None:
    now = datetime.now(UTC)
    with Session(committed_database) as session:
        user = add_user(session, linked_at=now - timedelta(days=1))
        add_subscription(session, user, active_since=now - timedelta(days=1))
        add_vacancy(
            session, company=None, description=JOB_TEXT, first_seen_at=now - timedelta(hours=1)
        )
        session.commit()
    match_task.apply()
    with Session(committed_database) as session:
        add_vacancy(session, source="dou", company="Acme", description=JOB_TEXT)
        session.commit()

    result = match_task.apply().result

    # The DOU posting joins the cluster first seen an hour ago: the same job, not news.
    assert result == {"opened": 0, "joined": 1, "matched": 0, "queued": 0}
    assert statuses(committed_database) == ["pending"]


def test_match_goes_on_while_another_run_is_clustering(committed_database: Engine) -> None:
    seed(committed_database)

    with committed_database.connect() as other, capture_logs() as logs:
        other.execute(select(func.pg_advisory_lock(locks.CLUSTER, 0)))
        try:
            result = match_task.apply().result
        finally:
            other.execute(select(func.pg_advisory_unlock(locks.CLUSTER, 0)))

    assert result == {"opened": 0, "joined": 0, "matched": 0, "queued": 0}
    assert "clusters.busy" in [log["event"] for log in logs]


def test_send_delivers_once(committed_database: Engine, api: respx.MockRouter) -> None:
    route = api.post("/sendMessage").respond(
        200, content=fixture_bytes("telegram/send_message.json")
    )
    seed(committed_database)
    match_task.apply()

    first = send_task.apply()
    second = send_task.apply()

    assert first.successful(), first.result
    assert first.result == {**NOTHING_SENT, "sent": 1}
    assert second.result == NOTHING_SENT
    assert route.call_count == 1
    assert statuses(committed_database) == ["sent"]


def test_send_after_429_leaves_the_delivery_for_a_later_run(
    committed_database: Engine, api: respx.MockRouter
) -> None:
    route = api.post("/sendMessage").respond(
        429, content=fixture_bytes("telegram/too_many_requests.json")
    )
    seed(committed_database)
    match_task.apply()

    assert send_task.apply().result == NOTHING_SENT
    # No retry of its own: the next run sends once `next_attempt_at` has come.
    assert send_task.apply().result == NOTHING_SENT
    assert route.call_count == 1
    assert statuses(committed_database) == ["pending"]


def test_send_waits_for_a_run_already_sending(
    committed_database: Engine, api: respx.MockRouter
) -> None:
    route = api.post("/sendMessage").respond(
        200, content=fixture_bytes("telegram/send_message.json")
    )
    seed(committed_database)
    match_task.apply()

    with sender_lock(committed_database), capture_logs() as logs:
        result = send_task.apply()

    assert result.result == NOTHING_SENT
    assert not route.called
    assert "notify.send_busy" in [log["event"] for log in logs]


def test_send_without_a_token_sends_nothing(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    route = api.post("/sendMessage").respond(200)

    with capture_logs() as logs:
        result = send_task.apply()

    assert result.result == NOTHING_SENT
    assert not route.called
    assert [log["event"] for log in logs] == ["telegram.not_configured"]


def test_send_with_a_rejected_token_fails(
    committed_database: Engine, api: respx.MockRouter
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
    committed_database: Engine, api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.post("/sendMessage").respond(200, content=fixture_bytes("telegram/send_message.json"))
    hosts: list[str] = []
    monkeypatch.setattr(
        "vacancy_radar.ratelimit.wait_for_token", lambda _bucket, host: hosts.append(host)
    )
    seed(committed_database)
    match_task.apply()

    send_task.apply()

    assert hosts == ["api.telegram.org"]


def test_match_warns_about_deliveries_left_sending(committed_database: Engine) -> None:
    seed(committed_database)
    match_task.apply()
    with Session(committed_database) as session:
        session.execute(
            update(Delivery).values(
                status="sending", claimed_at=datetime.now(UTC) - timedelta(hours=1)
            )
        )
        session.commit()

    with capture_logs() as logs:
        match_task.apply()

    assert {"event": "notify.stuck_deliveries", "count": 1, "log_level": "warning"} in logs
