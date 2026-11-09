"""Sending deliveries, with the Bot API mocked by respx.

Which failure leads to which outcome is tested in `test_outcome.py`; these
tests check that `send_pending` stores and acts on it.
"""

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
import redis
import respx
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from tests.notify.factories import (
    CHAT_ID,
    NOW,
    add_clustered_vacancy,
    add_subscription,
    add_user,
    add_vacancy,
)
from tests.support import fixture_bytes
from vacancy_radar.db.models import Delivery, User, Vacancy
from vacancy_radar.notify import sending
from vacancy_radar.notify.matching import match_new
from vacancy_radar.notify.outcome import BACKOFF_MAX_SECONDS, MAX_ATTEMPTS, OUTCOME_UNKNOWN
from vacancy_radar.notify.sending import (
    MAX_DELIVERIES_PER_RUN,
    NO_VACANCY,
    STUCK_AFTER,
    SendResult,
    claim_next,
    count_stuck,
    send_pending,
    sender_lock,
)
from vacancy_radar.ratelimit import TokenBucket
from vacancy_radar.telegram.client import (
    API_URL,
    TelegramClient,
    TelegramUnauthorizedError,
    build_http,
)

TOKEN = "7000000001:AAtest-token"
SITE = "https://radar.example"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=f"{API_URL}/bot{TOKEN}", assert_all_called=False) as router:
        yield router


@pytest.fixture
def client() -> Iterator[TelegramClient]:
    with build_http() as http:
        yield TelegramClient(http, TOKEN)


def reply(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=fixture_bytes(f"telegram/{name}.json"))


def send(
    session: Session, client: TelegramClient, *, limit: int = MAX_DELIVERIES_PER_RUN
) -> SendResult:
    return send_pending(session, client, site_url=SITE, clock=lambda: NOW, limit=limit)


# Each title gets a skill of its own, so a subscription queued for one
# vacancy does not match the others.
SKILL_OF = {"Python Developer": "python", "First": "java", "Second": "go", "Third": "rust"}


def queue_one(session: Session, user: User | None = None, title: str = "Python Developer") -> None:
    """Queue one delivery: a new subscription of `user` and a vacancy only it matches."""
    skill = SKILL_OF[title]
    add_subscription(session, user or add_user(session), f"Wants {skill}", skills=[skill])
    add_clustered_vacancy(session, title=title, skills=[skill])
    assert match_new(session, now=NOW).queued == 1


def queue_two_accounts(session: Session) -> None:
    queue_one(session, title="First")
    queue_one(session, add_user(session, "other@example.com", chat_id=987), title="Second")


def deliveries(session: Session) -> list[Delivery]:
    session.expire_all()
    return list(session.scalars(select(Delivery).order_by(Delivery.id)))


def test_sends_a_delivery_once(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    queue_one(db_session)

    assert send(db_session, client) == SendResult(sent=1)
    assert send(db_session, client) == SendResult()

    assert route.call_count == 1
    payload = json.loads(route.calls.last.request.content)
    assert payload["chat_id"] == CHAT_ID
    assert "<b>Python Developer</b>" in payload["text"]
    [row] = deliveries(db_session)
    assert (row.status, row.sent_at, row.telegram_message_id, row.attempts) == ("sent", NOW, 42, 1)


def test_one_message_names_every_subscription_that_matched(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    user = add_user(db_session)
    add_subscription(db_session, user, "Python")
    add_subscription(db_session, user, "Backend", skills=["python", "go"])
    add_clustered_vacancy(db_session)
    match_new(db_session, now=NOW)

    assert send(db_session, client) == SendResult(sent=1)

    assert route.call_count == 1
    assert "Matched: Backend, Python" in json.loads(route.calls.last.request.content)["text"]


def test_a_chat_unlinked_after_matching_is_skipped(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    user = add_user(db_session)
    queue_one(db_session, user)
    user.telegram_chat_id = None
    user.telegram_linked_at = None
    db_session.flush()

    assert send(db_session, client) == SendResult(skipped=1)
    assert not route.called
    assert [row.status for row in deliveries(db_session)] == ["skipped"]


def test_a_delivery_waiting_for_a_retry_is_not_sent_early(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    queue_one(db_session)
    [row] = deliveries(db_session)
    row.next_attempt_at = NOW + timedelta(seconds=1)
    db_session.flush()

    assert send(db_session, client) == SendResult()
    assert not route.called


def test_429_waits_retry_after_and_stops_the_run(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("too_many_requests", 429))
    queue_two_accounts(db_session)

    assert send(db_session, client) == SendResult()

    assert route.call_count == 1
    first, second = deliveries(db_session)
    assert (first.status, first.attempts) == ("pending", 0)
    assert first.next_attempt_at == NOW + timedelta(seconds=17)
    assert (second.status, second.attempts, second.next_attempt_at) == ("pending", 0, NOW)


def test_a_request_that_never_left_waits_a_backoff_and_stops_the_run(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(side_effect=[httpx.ConnectError("refused")])
    queue_one(db_session)

    assert send(db_session, client) == SendResult()
    assert route.call_count == 1

    [row] = deliveries(db_session)
    assert (row.status, row.attempts) == ("pending", 1)
    assert NOW + timedelta(seconds=1) <= row.next_attempt_at
    assert row.next_attempt_at <= NOW + timedelta(seconds=BACKOFF_MAX_SECONDS)


def test_fails_after_the_last_attempt(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(side_effect=httpx.ConnectError("refused"))
    queue_one(db_session)
    [row] = deliveries(db_session)
    row.attempts = MAX_ATTEMPTS - 1
    db_session.flush()

    assert send(db_session, client) == SendResult(failed=1)
    [row] = deliveries(db_session)
    assert (row.status, row.attempts) == ("failed", MAX_ATTEMPTS)
    assert row.error == "sendMessage: ConnectError"


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (httpx.ReadTimeout("timed out"), "sendMessage: ReadTimeout"),
        (httpx.RemoteProtocolError("disconnected"), "sendMessage: RemoteProtocolError"),
        (httpx.Response(502, text="Bad Gateway"), "sendMessage: 502 HTTP 502 without Bot API JSON"),
        (
            httpx.Response(500, json={"ok": False, "error_code": 500, "description": "Internal"}),
            "sendMessage: 500 Internal",
        ),
    ],
    ids=["read timeout", "dropped", "gateway 502", "bot api 500"],
)
def test_a_request_that_may_have_arrived_is_never_sent_again(
    db_session: Session,
    api: respx.MockRouter,
    client: TelegramClient,
    failure: httpx.Response | Exception,
    error: str,
) -> None:
    route = api.post("/sendMessage").mock(side_effect=[failure, reply("send_message")])
    queue_two_accounts(db_session)

    # The run stops: Telegram may be failing for everyone.
    assert send(db_session, client) == SendResult(failed=1)
    assert send(db_session, client) == SendResult(sent=1)
    assert send(db_session, client) == SendResult()

    assert route.call_count == 2
    first, second = deliveries(db_session)
    assert (first.status, first.error) == ("failed", f"{OUTCOME_UNKNOWN}: {error}")
    assert second.status == "sent"


@pytest.mark.parametrize(("fixture", "status"), [("bot_blocked", 403), ("chat_not_found", 400)])
def test_a_chat_that_is_gone_is_unlinked(
    db_session: Session,
    api: respx.MockRouter,
    client: TelegramClient,
    fixture: str,
    status: int,
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply(fixture, status))
    user = add_user(db_session)
    queue_one(db_session, user, title="First")
    queue_one(db_session, user, title="Second")

    with capture_logs() as logs:
        assert send(db_session, client) == SendResult(failed=1, skipped=1)

    assert route.call_count == 1
    assert [row.status for row in deliveries(db_session)] == ["failed", "skipped"]
    db_session.refresh(user)
    assert (user.telegram_chat_id, user.telegram_linked_at) == (None, None)
    assert [(log["user"], log["via"]) for log in logs if log["event"] == "telegram.unlinked"] == [
        (user.id, "blocked")
    ]


def test_a_rejected_token_puts_the_delivery_back_and_raises(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(return_value=reply("unauthorized", 401))
    queue_one(db_session)

    with pytest.raises(TelegramUnauthorizedError):
        send(db_session, client)

    [row] = deliveries(db_session)
    assert (row.status, row.attempts, row.next_attempt_at) == ("pending", 0, NOW)


def test_any_other_refusal_fails_the_delivery_and_goes_on(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(
        side_effect=[
            httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad"}),
            reply("send_message"),
        ]
    )
    queue_two_accounts(db_session)

    assert send(db_session, client) == SendResult(sent=1, failed=1)
    first, second = deliveries(db_session)
    assert (first.status, first.error) == ("failed", "sendMessage: 400 Bad")
    assert second.status == "sent"


def test_a_redis_failure_in_the_rate_limiter_puts_the_delivery_back(
    db_session: Session, api: respx.MockRouter
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    broken = Mock(spec=TokenBucket)
    broken.acquire.side_effect = redis.ConnectionError("Connection refused")
    queue_one(db_session)

    with build_http(rate_limiter=broken) as http:
        assert send(db_session, TelegramClient(http, TOKEN)) == SendResult()

    assert not route.called
    [row] = deliveries(db_session)
    # Not left `sending`: the request certainly never left.
    assert (row.status, row.attempts) == ("pending", 1)


def test_an_error_of_ours_before_the_request_puts_the_delivery_back_and_raises(
    db_session: Session,
    api: respx.MockRouter,
    client: TelegramClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    monkeypatch.setattr(sending, "render_message", Mock(side_effect=KeyError("oops")))
    queue_one(db_session)

    with pytest.raises(KeyError):
        send(db_session, client)

    assert not route.called
    [row] = deliveries(db_session)
    assert (row.status, row.attempts) == ("pending", 1)
    assert row.error == "KeyError: 'oops'"


def test_the_message_shows_a_vacancy_the_cluster_still_has(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    queue_one(db_session)
    db_session.execute(update(Vacancy).values(title="Renamed Python Developer"))

    assert send(db_session, client) == SendResult(sent=1)
    assert "<b>Renamed Python Developer</b>" in json.loads(route.calls.last.request.content)["text"]


def add_member(session: Session, title: str, **columns: Any) -> None:
    """Another vacancy in the cluster of the only queued delivery."""
    [delivery] = deliveries(session)
    add_vacancy(session, title=title, cluster_id=delivery.cluster_id, **columns)


def shown_title(session: Session) -> str:
    claim = claim_next(session, now=NOW)
    assert claim is not None
    assert claim.card is not None
    return claim.card.title


def test_the_message_shows_a_member_with_a_salary_first(db_session: Session) -> None:
    queue_one(db_session)  # "Python Developer": a company, no salary
    add_member(
        db_session,
        "With salary",
        company=None,
        salary_min=3000,
        salary_max=4000,
        salary_currency="USD",
        salary_period="month",
    )

    assert shown_title(db_session) == "With salary"


def test_the_message_shows_a_member_with_a_company_next(db_session: Session) -> None:
    queue_one(db_session)
    db_session.execute(update(Vacancy).values(company=None))
    add_member(db_session, "With company", company="Acme")

    assert shown_title(db_session) == "With company"


def test_of_equally_complete_members_the_message_shows_the_first(db_session: Session) -> None:
    queue_one(db_session)
    add_member(db_session, "Later", company="Acme")

    assert shown_title(db_session) == "Python Developer"


def test_a_cluster_without_vacancies_is_skipped(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    queue_one(db_session)
    db_session.execute(update(Vacancy).values(cluster_id=None))

    assert send(db_session, client) == SendResult(skipped=1)
    assert not route.called
    [row] = deliveries(db_session)
    assert (row.status, row.error) == ("skipped", NO_VACANCY)


def test_a_claim_records_when_it_was_made(db_session: Session) -> None:
    queue_one(db_session)

    claim_next(db_session, now=NOW)

    [row] = deliveries(db_session)
    assert (row.status, row.claimed_at) == ("sending", NOW)
    assert count_stuck(db_session, now=NOW + STUCK_AFTER - timedelta(seconds=1)) == 0
    assert count_stuck(db_session, now=NOW + STUCK_AFTER + timedelta(seconds=1)) == 1


def test_a_run_handles_at_most_its_limit(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(return_value=reply("send_message"))
    user = add_user(db_session)
    for title in ("First", "Second", "Third"):
        queue_one(db_session, user, title=title)

    assert send(db_session, client, limit=2) == SendResult(sent=2)
    assert send(db_session, client, limit=2) == SendResult(sent=1)


def test_a_claim_skips_a_delivery_another_sender_holds(committed_database: Engine) -> None:
    with Session(committed_database) as setup:
        queue_two_accounts(setup)
        setup.commit()
        first_id = setup.scalars(select(Delivery.id).order_by(Delivery.id)).first()

    with Session(committed_database) as other, Session(committed_database) as sender:
        other.execute(select(Delivery).where(Delivery.id == first_id).with_for_update())

        claim = claim_next(sender, now=NOW)

        assert claim is not None
        assert claim.card is not None
        assert claim.card.title == "Second"
        other.rollback()
        assert claim_next(sender, now=NOW) is not None
        assert claim_next(sender, now=NOW) is None


def test_only_one_sender_at_a_time(committed_database: Engine) -> None:
    with sender_lock(committed_database) as first, sender_lock(committed_database) as second:
        assert (first, second) == (True, False)
    with sender_lock(committed_database) as again:
        assert again
