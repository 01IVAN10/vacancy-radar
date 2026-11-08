"""Delivering queued notifications, with the Bot API mocked by respx."""

import json
from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.notify.factories import (
    CHAT_ID,
    NOW,
    add_clustered_vacancy,
    add_subscription,
    add_user,
)
from tests.support import fixture_bytes
from vacancy_radar.db.models import Notification, User
from vacancy_radar.notify.matching import match_new
from vacancy_radar.notify.sending import (
    BACKOFF_MAX_SECONDS,
    MAX_ATTEMPTS,
    MAX_DELIVERIES_PER_RUN,
    OUTCOME_UNKNOWN,
    SendResult,
    claim_next,
    send_pending,
)
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


def reply(name: str) -> httpx.Response:
    return httpx.Response(200, content=fixture_bytes(f"telegram/{name}.json"))


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
    assert match_new(session, now=NOW) == 1


def notifications(session: Session) -> list[Notification]:
    session.expire_all()
    return list(session.scalars(select(Notification).order_by(Notification.id)))


def test_sends_a_match_once(
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
    [row] = notifications(db_session)
    assert (row.status, row.sent_at, row.telegram_message_id, row.attempts) == ("sent", NOW, 42, 1)


def test_one_message_for_every_subscription_a_cluster_matched(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    user = add_user(db_session)
    add_subscription(db_session, user, "Python")
    add_subscription(db_session, user, "Backend", skills=["python", "go"])
    add_clustered_vacancy(db_session)
    assert match_new(db_session, now=NOW) == 2

    assert send(db_session, client) == SendResult(sent=1)

    assert route.call_count == 1
    assert "Matched: Backend, Python" in json.loads(route.calls.last.request.content)["text"]
    assert [row.status for row in notifications(db_session)] == ["sent", "sent"]


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
    assert [row.status for row in notifications(db_session)] == ["skipped"]


def test_a_delivery_waiting_for_a_retry_is_not_sent_early(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))
    queue_one(db_session)
    [row] = notifications(db_session)
    row.next_attempt_at = NOW + timedelta(seconds=1)
    db_session.flush()

    assert send(db_session, client) == SendResult()
    assert not route.called


def test_429_puts_the_delivery_back_for_retry_after_and_stops(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(
        return_value=httpx.Response(429, content=fixture_bytes("telegram/too_many_requests.json"))
    )
    queue_one(db_session, title="First")
    queue_one(db_session, add_user(db_session, "other@example.com", chat_id=987), title="Second")

    assert send(db_session, client) == SendResult(retry_in=17)

    assert route.call_count == 1
    first, second = notifications(db_session)
    assert (first.status, first.attempts) == ("pending", 0)
    assert first.next_attempt_at == NOW + timedelta(seconds=17)
    assert (second.status, second.attempts) == ("pending", 0)


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(502, text="Bad Gateway"), httpx.ConnectError("refused")],
    ids=["5xx", "no connection"],
)
def test_a_failure_that_sent_nothing_is_retried_with_backoff(
    db_session: Session,
    api: respx.MockRouter,
    client: TelegramClient,
    failure: httpx.Response | Exception,
) -> None:
    api.post("/sendMessage").mock(side_effect=[failure])
    queue_one(db_session)

    result = send(db_session, client)

    assert result.retry_in is not None
    assert 1 <= result.retry_in <= BACKOFF_MAX_SECONDS
    [row] = notifications(db_session)
    assert (row.status, row.attempts) == ("pending", 1)
    assert row.next_attempt_at == NOW + timedelta(seconds=result.retry_in)


def test_fails_after_the_last_attempt(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(return_value=httpx.Response(502, text="Bad Gateway"))
    queue_one(db_session)
    [row] = notifications(db_session)
    row.attempts = MAX_ATTEMPTS - 1
    db_session.flush()

    assert send(db_session, client) == SendResult(failed=1)
    [row] = notifications(db_session)
    assert (row.status, row.attempts) == ("failed", MAX_ATTEMPTS)
    assert row.error == "sendMessage: 502 HTTP 502 without Bot API JSON"


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("timed out"), httpx.RemoteProtocolError("disconnected")]
)
def test_a_request_that_may_have_arrived_is_not_sent_again(
    db_session: Session, api: respx.MockRouter, client: TelegramClient, error: Exception
) -> None:
    route = api.post("/sendMessage").mock(side_effect=[error, reply("send_message")])
    queue_one(db_session, title="First")
    queue_one(db_session, add_user(db_session, "other@example.com", chat_id=987), title="Second")

    # The run goes on with the next delivery.
    assert send(db_session, client) == SendResult(sent=1, failed=1)
    assert send(db_session, client) == SendResult()

    assert route.call_count == 2
    first, second = notifications(db_session)
    assert first.status == "failed"
    assert first.error == f"{OUTCOME_UNKNOWN}: sendMessage: {type(error).__name__}"
    assert second.status == "sent"


def test_a_blocked_bot_fails_the_delivery_and_unlinks_the_chat(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(
        return_value=httpx.Response(403, content=fixture_bytes("telegram/bot_blocked.json"))
    )
    user = add_user(db_session)
    queue_one(db_session, user, title="First")
    queue_one(db_session, user, title="Second")

    assert send(db_session, client) == SendResult(failed=1, skipped=1)

    assert route.call_count == 1
    assert [row.status for row in notifications(db_session)] == ["failed", "skipped"]
    db_session.refresh(user)
    assert (user.telegram_chat_id, user.telegram_linked_at) == (None, None)


def test_a_rejected_token_puts_the_delivery_back_and_raises(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(
        return_value=httpx.Response(401, content=fixture_bytes("telegram/unauthorized.json"))
    )
    queue_one(db_session)

    with pytest.raises(TelegramUnauthorizedError):
        send(db_session, client)

    [row] = notifications(db_session)
    assert (row.status, row.attempts, row.next_attempt_at) == ("pending", 0, NOW)


def test_any_other_refusal_fails_the_delivery_and_goes_on(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(
        side_effect=[
            httpx.Response(400, content=fixture_bytes("telegram/chat_not_found.json")),
            reply("send_message"),
        ]
    )
    queue_one(db_session, title="First")
    queue_one(db_session, add_user(db_session, "other@example.com", chat_id=987), title="Second")

    assert send(db_session, client) == SendResult(sent=1, failed=1)
    first, second = notifications(db_session)
    assert first.status == "failed"
    assert first.error is not None
    assert first.error.startswith("sendMessage: 400")
    assert second.status == "sent"


def test_a_run_sends_at_most_its_limit(
    db_session: Session, api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(return_value=reply("send_message"))
    user = add_user(db_session)
    for title in ("First", "Second", "Third"):
        queue_one(db_session, user, title=title)

    assert send(db_session, client, limit=2) == SendResult(sent=2)
    assert send(db_session, client, limit=2) == SendResult(sent=1)


def test_a_claim_skips_an_account_another_sender_holds(committed_database: Engine) -> None:
    with Session(committed_database) as setup:
        first = add_user(setup, "first@example.com", chat_id=1)
        queue_one(setup, first, title="First")
        queue_one(setup, add_user(setup, "second@example.com", chat_id=2), title="Second")
        setup.commit()
        first_id = first.id

    with Session(committed_database) as other, Session(committed_database) as sender:
        # Another sender is in the middle of claiming for the first account.
        other.execute(select(User).where(User.id == first_id).with_for_update())

        delivery = claim_next(sender, now=NOW)

        assert delivery is not None
        assert delivery.chat_id == 2
        assert delivery.card.title == "Second"
        other.rollback()
        assert claim_next(sender, now=NOW) is not None
        assert claim_next(sender, now=NOW) is None
