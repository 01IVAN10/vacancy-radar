import json
import logging
from collections.abc import Iterator
from unittest.mock import Mock, patch

import httpx
import pytest
import respx
import structlog

from tests.support import fixture_bytes
from vacancy_radar import __version__
from vacancy_radar.log import configure_logging
from vacancy_radar.sources.ratelimit import RateLimitError, TokenBucket
from vacancy_radar.telegram.client import (
    API_URL,
    DEFAULT_RETRY_AFTER_SECONDS,
    LONG_POLL_MARGIN_SECONDS,
    Chat,
    Message,
    TelegramApiError,
    TelegramClient,
    TelegramConflictError,
    TelegramError,
    TelegramForbiddenError,
    TelegramMalformedError,
    TelegramRateLimitedError,
    TelegramServerError,
    TelegramUnauthorizedError,
    TelegramUnavailableError,
    Update,
    build_http,
)

TOKEN = "7000000001:AAtest-token-that-must-never-be-logged"
BOT_URL = f"{API_URL}/bot{TOKEN}"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BOT_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client() -> Iterator[TelegramClient]:
    with build_http() as http:
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
    ("fixture", "status", "error_type", "retryable"),
    [
        ("too_many_requests", 429, TelegramRateLimitedError, True),
        ("unauthorized", 401, TelegramUnauthorizedError, False),
        ("bot_blocked", 403, TelegramForbiddenError, False),
        ("conflict", 409, TelegramConflictError, False),
        ("chat_not_found", 400, TelegramApiError, False),
    ],
)
def test_an_error_answer_is_classified(
    api: respx.MockRouter,
    client: TelegramClient,
    fixture: str,
    status: int,
    error_type: type[TelegramApiError],
    retryable: bool,
) -> None:
    api.post("/sendMessage").mock(return_value=reply(fixture, status))

    with pytest.raises(error_type) as caught:
        client.send_message(123456789, "Hello")

    error = caught.value
    assert type(error) is error_type
    assert (error.method, error.error_code, error.retryable) == ("sendMessage", status, retryable)
    assert str(error).startswith(f"sendMessage: {status} ")


def test_429_carries_how_long_to_wait(api: respx.MockRouter, client: TelegramClient) -> None:
    api.post("/sendMessage").mock(return_value=reply("too_many_requests", 429))

    with pytest.raises(TelegramRateLimitedError) as caught:
        client.send_message(123456789, "Hello")

    assert caught.value.retry_after == 17


def test_429_without_retry_after_waits_the_default(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    body = {"ok": False, "error_code": 429, "description": "Too Many Requests"}
    api.post("/sendMessage").mock(return_value=httpx.Response(429, json=body))

    with pytest.raises(TelegramRateLimitedError) as caught:
        client.send_message(123456789, "Hello")

    assert caught.value.retry_after == DEFAULT_RETRY_AFTER_SECONDS


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            httpx.Response(
                500, json={"ok": False, "error_code": 500, "description": "Internal Server Error"}
            ),
            id="bot-api-json",
        ),
        pytest.param(httpx.Response(502, text="<html>Bad Gateway</html>"), id="proxy-html"),
    ],
)
def test_a_server_error_is_retryable(
    api: respx.MockRouter, client: TelegramClient, response: httpx.Response
) -> None:
    api.post("/sendMessage").mock(return_value=response)

    with pytest.raises(TelegramServerError) as caught:
        client.send_message(123456789, "Hello")

    assert caught.value.retryable
    assert caught.value.error_code == response.status_code


@pytest.mark.parametrize(
    ("method", "response"),
    [
        pytest.param(
            "getMe", httpx.Response(200, json={"ok": True, "result": {"id": 1}}), id="no-username"
        ),
        pytest.param(
            "getMe", httpx.Response(200, json={"ok": True, "result": "nope"}), id="not-an-object"
        ),
        pytest.param("getMe", httpx.Response(200, text="<html>Welcome</html>"), id="not-json"),
        pytest.param("getMe", httpx.Response(404, text="Not Found"), id="not-json-4xx"),
        pytest.param(
            "getUpdates", httpx.Response(200, json={"ok": True, "result": {}}), id="not-a-list"
        ),
    ],
)
def test_an_answer_not_shaped_like_the_bot_api_is_malformed(
    api: respx.MockRouter, client: TelegramClient, method: str, response: httpx.Response
) -> None:
    api.post(f"/{method}").mock(return_value=response)

    with pytest.raises(TelegramMalformedError) as caught:
        client.get_me() if method == "getMe" else client.get_updates(offset=None, timeout=0)

    assert not caught.value.retryable


def test_an_update_without_an_id_does_not_hide_the_others(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    result = [{"message": {"chat": {"id": 1, "type": "private"}}}, {"update_id": 9}]
    api.post("/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": result})
    )

    assert client.get_updates(offset=None, timeout=0) == [Update(9, None)]


def test_a_long_poll_waits_longer_than_telegram_holds_it(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    polled = api.post("/getUpdates").mock(return_value=reply("get_updates_empty"))
    sent = api.post("/sendMessage").mock(return_value=reply("send_message"))

    client.get_updates(offset=None, timeout=50)
    client.send_message(1, "Hello")

    poll_timeout = polled.calls.last.request.extensions["timeout"]
    assert poll_timeout["read"] == 50 + LONG_POLL_MARGIN_SECONDS
    # Other calls keep the client's own, short timeout.
    assert sent.calls.last.request.extensions["timeout"]["read"] == 10


def test_the_bot_api_client_is_its_own() -> None:
    with build_http() as http:
        assert http.headers["User-Agent"] == f"vacancy-radar/{__version__}"
        assert http.timeout.read == 10


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

    assert caught.value.retryable
    assert isinstance(caught.value, TelegramError)
    assert str(caught.value) == f"sendMessage: {type(error).__name__}"
    # Not chained: an httpx error can quote the URL, and the URL holds the token.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    ("error", "maybe_delivered"),
    [
        (httpx.ConnectError("refused"), False),
        (httpx.ConnectTimeout("timed out"), False),
        (httpx.PoolTimeout("no free connection"), False),
        (httpx.ReadTimeout("timed out"), True),
        (httpx.WriteTimeout("timed out"), True),
        (httpx.ReadError("connection reset"), True),
        (httpx.RemoteProtocolError("server disconnected"), True),
    ],
)
def test_a_transport_failure_says_whether_the_request_may_have_arrived(
    api: respx.MockRouter,
    client: TelegramClient,
    error: httpx.HTTPError,
    maybe_delivered: bool,
) -> None:
    api.post("/sendMessage").mock(side_effect=error)

    with pytest.raises(TelegramUnavailableError) as caught:
        client.send_message(123456789, "Hello")

    assert caught.value.maybe_delivered is maybe_delivered


def test_requests_wait_for_a_rate_limit_token(api: respx.MockRouter) -> None:
    hosts: list[str] = []
    api.post("/getMe").mock(return_value=reply("get_me"))

    with patch("vacancy_radar.sources.http.wait_for_token") as wait:
        wait.side_effect = lambda _bucket, host: hosts.append(host)
        with build_http(rate_limiter=Mock(spec=TokenBucket)) as http:
            TelegramClient(http, TOKEN).get_me()

    assert hosts == ["api.telegram.org"]


def test_no_rate_limit_token_means_the_request_was_never_sent(api: respx.MockRouter) -> None:
    route = api.post("/sendMessage").mock(return_value=reply("send_message"))

    with (
        patch("vacancy_radar.sources.http.wait_for_token", side_effect=RateLimitError),
        build_http(rate_limiter=Mock(spec=TokenBucket)) as http,
        pytest.raises(TelegramUnavailableError) as caught,
    ):
        TelegramClient(http, TOKEN).send_message(123456789, "Hello")

    assert not caught.value.maybe_delivered
    assert not route.called


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
