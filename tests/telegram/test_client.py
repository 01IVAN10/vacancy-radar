import json
import logging
from collections.abc import Iterator

import httpx
import pytest
import respx
import structlog

from tests.support import fixture_bytes
from vacancy_radar.log import configure_logging
from vacancy_radar.telegram.client import (
    API_URL,
    Chat,
    Message,
    TelegramClient,
    TelegramError,
    TelegramUnavailableError,
    Update,
)

TOKEN = "7000000001:AAtest-token-that-must-never-be-logged"
BOT_URL = f"{API_URL}/bot{TOKEN}"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BOT_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client() -> Iterator[TelegramClient]:
    with httpx.Client() as http:
        yield TelegramClient(http, TOKEN)


def reply(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=fixture_bytes(f"telegram/{name}.json"))


def sent_json(route: respx.Route) -> object:
    return json.loads(route.calls.last.request.content)


def test_get_me_returns_the_username(api: respx.MockRouter, client: TelegramClient) -> None:
    api.post("/getMe").mock(return_value=reply("get_me"))

    assert client.get_me() == "vacancy_radar_test_bot"


def test_get_updates_parses_the_messages_the_bot_handles(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/getUpdates").mock(return_value=reply("get_updates"))

    updates = client.get_updates(offset=500000001, timeout=20)

    assert updates == [
        Update(500000001, Message(Chat(123456789, "private"), "/start TOKEN")),
        Update(500000002, Message(Chat(123456789, "private"), None)),  # a sticker
        Update(500000003, Message(Chat(-1001234567890, "supergroup"), "/start")),
        Update(500000004, None),  # an edited message
    ]
    assert sent_json(route) == {"timeout": 20, "allowed_updates": ["message"], "offset": 500000001}


def test_the_first_poll_sends_no_offset(api: respx.MockRouter, client: TelegramClient) -> None:
    route = api.post("/getUpdates").mock(return_value=reply("get_updates_empty"))

    assert client.get_updates(offset=None, timeout=20) == []
    assert sent_json(route) == {"timeout": 20, "allowed_updates": ["message"]}


def test_send_message_sends_html_without_a_preview(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))

    assert client.send_message(123456789, "<b>Python</b> developer") == 42
    assert sent_json(route) == {
        "chat_id": 123456789,
        "text": "<b>Python</b> developer",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }


@pytest.mark.parametrize(
    ("fixture", "status", "error_code", "retry_after"),
    [
        ("too_many_requests", 429, 429, 17),
        ("bot_blocked", 403, 403, None),
        ("chat_not_found", 400, 400, None),
        ("conflict", 409, 409, None),
        ("unauthorized", 401, 401, None),
    ],
)
def test_an_error_answer_raises_telegram_error(
    api: respx.MockRouter,
    client: TelegramClient,
    fixture: str,
    status: int,
    error_code: int,
    retry_after: int | None,
) -> None:
    api.post("/sendMessage").mock(return_value=reply(fixture, status))

    with pytest.raises(TelegramError) as caught:
        client.send_message(123456789, "Hello")

    error = caught.value
    assert (error.method, error.error_code, error.retry_after) == (
        "sendMessage",
        error_code,
        retry_after,
    )
    assert str(error).startswith(f"sendMessage: {error_code} ")


def test_an_answer_that_is_not_bot_api_json_raises_with_the_http_status(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/sendMessage").mock(return_value=httpx.Response(502, text="<html>Bad Gateway</html>"))

    with pytest.raises(TelegramError) as caught:
        client.send_message(123456789, "Hello")

    assert caught.value.error_code == 502


@pytest.mark.parametrize(
    "result",
    [
        pytest.param({"ok": True, "result": {"id": 1}}, id="getMe-without-username"),
        pytest.param({"ok": True, "result": "nope"}, id="result-not-an-object"),
    ],
)
def test_an_unexpected_result_shape_raises(
    api: respx.MockRouter, client: TelegramClient, result: object
) -> None:
    api.post("/getMe").mock(return_value=httpx.Response(200, json=result))

    with pytest.raises(TelegramError, match="unexpected result shape"):
        client.get_me()


def test_get_updates_refuses_a_result_that_is_not_a_list(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/getUpdates").mock(return_value=httpx.Response(200, json={"ok": True, "result": {}}))

    with pytest.raises(TelegramError, match="unexpected result shape"):
        client.get_updates(offset=None, timeout=0)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("[Errno 8] nodename nor servname provided"),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_a_transport_failure_raises_unavailable(
    api: respx.MockRouter, client: TelegramClient, error: httpx.HTTPError
) -> None:
    api.post("/sendMessage").mock(side_effect=error)

    with pytest.raises(TelegramUnavailableError) as caught:
        client.send_message(123456789, "Hello")

    assert str(caught.value) == f"sendMessage: {type(error).__name__}"
    # Not chained: an httpx error can quote the URL, and the URL holds the token.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.fixture
def logging_configured() -> Iterator[None]:
    configure_logging("INFO", json=True)
    yield
    structlog.reset_defaults()


@pytest.mark.usefixtures("logging_configured")
def test_the_token_reaches_neither_errors_nor_logs(
    api: respx.MockRouter, client: TelegramClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)  # as Celery's root handler would, or lower
    api.post("/sendMessage").mock(
        side_effect=[reply("send_message"), httpx.ConnectError(f"cannot reach {BOT_URL}")]
    )

    client.send_message(123456789, "Hello")
    with pytest.raises(TelegramUnavailableError) as caught:
        client.send_message(123456789, "Hello")

    assert TOKEN not in caplog.text
    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value)
