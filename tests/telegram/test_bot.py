from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.db.models import TelegramLinkToken, User
from vacancy_radar.telegram.__main__ import Sessions, poll_forever, poll_once
from vacancy_radar.telegram.bot import handle_update
from vacancy_radar.telegram.client import API_URL, Chat, Message, TelegramClient, Update
from vacancy_radar.telegram.linking import LINK_TOKEN_TTL, hash_token

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SITE = "https://radar.example"
CHAT = 123456789
TOKEN = "7000000001:AAtest-token"


def update(text: str | None, *, chat_type: str = "private", update_id: int = 1) -> Update:
    return Update(update_id, Message(Chat(CHAT, chat_type), text))


def answer(session: Session, text: str | None, *, chat_type: str = "private") -> str | None:
    return handle_update(session, update(text, chat_type=chat_type), now=NOW, site_url=SITE)


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(email="ivan@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def add_token(session: Session, user: User, token: str = "link-token") -> str:
    session.add(
        TelegramLinkToken(
            token_hash=hash_token(token), user_id=user.id, expires_at=NOW + LINK_TOKEN_TTL
        )
    )
    session.flush()
    return token


# --- handle_update ------------------------------------------------------------------


def test_start_with_a_token_links_the_chat(db_session: Session, user: User) -> None:
    reply = answer(db_session, f"/start {add_token(db_session, user)}")

    assert reply is not None
    assert "linked to <b>ivan@example.com</b>" in reply
    db_session.refresh(user)
    assert user.telegram_chat_id == CHAT


def test_start_addressed_to_the_bot_by_name_works_too(db_session: Session, user: User) -> None:
    answer(db_session, f"/start@vacancy_radar_bot {add_token(db_session, user)}")

    db_session.refresh(user)
    assert user.telegram_chat_id == CHAT


def test_start_with_a_used_token_says_so(db_session: Session, user: User) -> None:
    token = add_token(db_session, user)
    answer(db_session, f"/start {token}")

    reply = answer(db_session, f"/start {token}")

    assert reply is not None
    assert "expired or was already used" in reply
    assert f"{SITE}/telegram" in reply


@pytest.mark.parametrize("text", ["/start", "hello", "/help", "/START   "])
def test_anything_else_gets_the_help(db_session: Session, text: str) -> None:
    reply = answer(db_session, text)

    assert reply is not None
    assert "sends new IT vacancies" in reply


def test_stop_unlinks_the_chat(db_session: Session, user: User) -> None:
    answer(db_session, f"/start {add_token(db_session, user)}")

    reply = answer(db_session, "/stop")

    assert reply is not None
    assert "unlinked" in reply
    db_session.refresh(user)
    assert user.telegram_chat_id is None


def test_stop_in_a_chat_that_is_not_linked(db_session: Session) -> None:
    reply = answer(db_session, "/stop")

    assert reply is not None
    assert "not linked" in reply


@pytest.mark.parametrize(
    ("given", "chat_type"),
    [
        pytest.param(update("/start x", chat_type="supergroup"), "supergroup", id="group"),
        pytest.param(update(None), "private", id="no-text"),
        pytest.param(Update(1, None), "private", id="not-a-message"),
    ],
)
def test_no_reply_outside_private_text_messages(
    db_session: Session, user: User, given: Update, chat_type: str
) -> None:
    add_token(db_session, user, "x")

    assert handle_update(db_session, given, now=NOW, site_url=SITE) is None
    db_session.refresh(user)
    assert user.telegram_chat_id is None


def test_replies_escape_the_site_url(db_session: Session) -> None:
    reply = handle_update(db_session, update("/stop"), now=NOW, site_url="https://a.example/?x=<1>")

    assert reply is not None
    assert "<1>" not in reply
    assert "&lt;1&gt;" in reply


# --- the polling loop -------------------------------------------------------------


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=f"{API_URL}/bot{TOKEN}") as router:
        yield router


@pytest.fixture
def client() -> Iterator[TelegramClient]:
    with httpx.Client() as http:
        yield TelegramClient(http, TOKEN)


def sessions_of(session: Session) -> Sessions:
    """A session factory that hands out the test's session, which the fixture rolls back."""

    @contextmanager
    def shared() -> Iterator[Session]:
        yield session

    return shared


def fixture(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=fixture_bytes(f"telegram/{name}.json"))


def test_poll_once_answers_and_advances_the_offset(
    api: respx.MockRouter, client: TelegramClient, db_session: Session
) -> None:
    api.post("/getUpdates").mock(return_value=fixture("get_updates"))
    sent = api.post("/sendMessage").mock(return_value=fixture("send_message"))

    offset = poll_once(client, sessions_of(db_session), None, site_url=SITE)

    # The last update in the fixture is 500000004; the next poll confirms it.
    assert offset == 500000005
    # Only the private text message gets an answer: "/start TOKEN" with an unknown token.
    assert sent.call_count == 1
    assert b"expired or was already used" in sent.calls.last.request.content


def test_poll_once_without_updates_keeps_the_offset(
    api: respx.MockRouter, client: TelegramClient, db_session: Session
) -> None:
    api.post("/getUpdates").mock(return_value=fixture("get_updates_empty"))

    assert poll_once(client, sessions_of(db_session), 77, site_url=SITE) == 77


def test_a_failing_update_is_logged_and_still_confirmed(
    api: respx.MockRouter, client: TelegramClient, db_session: Session
) -> None:
    api.post("/getUpdates").mock(return_value=fixture("get_updates"))
    api.post("/sendMessage").mock(return_value=fixture("bot_blocked", 403))

    assert poll_once(client, sessions_of(db_session), None, site_url=SITE) == 500000005


def test_poll_forever_backs_off_after_a_failed_poll_and_stops(
    api: respx.MockRouter, client: TelegramClient, db_session: Session
) -> None:
    stop = Event()
    waits: list[float] = []

    def pause(delay: float) -> None:
        waits.append(delay)
        if len(waits) == 3:
            stop.set()

    api.post("/getUpdates").mock(return_value=fixture("conflict", 409))

    poll_forever(client, sessions_of(db_session), site_url=SITE, stop=stop, pause=pause)

    assert waits == [1, 2, 4]


def test_poll_forever_resets_the_backoff_after_a_good_poll(
    api: respx.MockRouter, client: TelegramClient, db_session: Session
) -> None:
    stop = Event()
    waits: list[float] = []
    polls = iter([fixture("conflict", 409), fixture("get_updates_empty"), fixture("conflict", 409)])

    def next_poll(request: httpx.Request) -> httpx.Response:
        response = next(polls, None)
        if response is None:
            stop.set()
            return fixture("get_updates_empty")
        return response

    api.post("/getUpdates").mock(side_effect=next_poll)

    poll_forever(client, sessions_of(db_session), site_url=SITE, stop=stop, pause=waits.append)

    assert waits == [1, 1]
