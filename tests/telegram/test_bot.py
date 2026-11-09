from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from vacancy_radar.db.models import TelegramLinkToken, User
from vacancy_radar.telegram.bot import handle_update
from vacancy_radar.telegram.client import Chat, Message, Update
from vacancy_radar.telegram.linking import LINK_TOKEN_TTL, hash_token

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SITE = "https://radar.example"
CHAT = 123456789


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


def test_start_says_when_the_chat_moves_from_another_account(db_session: Session) -> None:
    first = User(email="first@example.com", password_hash="x")
    second = User(email="second@example.com", password_hash="x")
    db_session.add_all([first, second])
    db_session.flush()
    answer(db_session, f"/start {add_token(db_session, first, 'first-token')}")

    reply = answer(db_session, f"/start {add_token(db_session, second, 'second-token')}")

    assert reply is not None
    assert "linked to <b>second@example.com</b>" in reply
    assert "linked to another account until now" in reply
    # The other account's email is not revealed.
    assert "first@example.com" not in reply


def test_start_does_not_mention_a_move_for_a_fresh_chat(db_session: Session, user: User) -> None:
    reply = answer(db_session, f"/start {add_token(db_session, user)}")

    assert reply is not None
    assert "another account" not in reply


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

    with capture_logs() as logs:
        reply = answer(db_session, "/stop")

    assert reply is not None
    assert "unlinked" in reply
    db_session.refresh(user)
    assert user.telegram_chat_id is None
    assert [(log["event"], log["via"]) for log in logs] == [("telegram.unlinked", "bot")]


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
