"""A small Telegram Bot API client on plain httpx, without a bot framework.

Only the three methods the project uses are wrapped: `getMe`, `getUpdates`
and `sendMessage`. Responses are parsed into typed dataclasses here, so the
rest of the code never handles raw JSON.

The bot token is part of every request URL (`/bot<token>/<method>`), so it
must never reach a log or an error message. httpx's own request log is
silenced in `log.py`, and every error raised from here names the method
only: transport errors are replaced by `TelegramUnavailableError`, without
the httpx exception chained, because some httpx errors quote the URL.
"""

from dataclasses import dataclass
from typing import Any, Final

import httpx

API_URL: Final = "https://api.telegram.org"


@dataclass(frozen=True, slots=True)
class Chat:
    id: int
    # "private", "group", "supergroup" or "channel".
    type: str


@dataclass(frozen=True, slots=True)
class Message:
    chat: Chat
    # None for messages without text, such as stickers or photos.
    text: str | None


@dataclass(frozen=True, slots=True)
class Update:
    update_id: int
    # None for every kind of update the bot does not handle.
    message: Message | None


class TelegramError(Exception):
    """The Bot API answered with `ok: false`, or with something that is not its JSON.

    `error_code` is Telegram's code, which follows HTTP: 400 for a bad
    request or an unknown chat, 403 when the user blocked the bot, 409 when
    another process is polling, 429 when sending too fast (`retry_after`
    seconds to wait), 5xx when Telegram itself fails.
    """

    def __init__(
        self, method: str, error_code: int, description: str, retry_after: int | None = None
    ) -> None:
        super().__init__(f"{method}: {error_code} {description}")
        self.method = method
        self.error_code = error_code
        self.description = description
        self.retry_after = retry_after


class TelegramUnavailableError(Exception):
    """No answer from the Bot API: a timeout or a network error. Worth retrying."""

    def __init__(self, method: str, reason: str) -> None:
        super().__init__(f"{method}: {reason}")
        self.method = method


class TelegramClient:
    """Calls Bot API methods with a bot token over a caller-owned httpx client."""

    def __init__(self, http: httpx.Client, token: str, *, api_url: str = API_URL) -> None:
        self._http = http
        self._base = f"{api_url}/bot{token}"

    def get_me(self) -> str:
        """The bot's username, which also proves the token is valid."""
        result = self._call("getMe", {})
        return _field(result, "username", str, "getMe")

    def get_updates(self, *, offset: int | None, timeout: int) -> list[Update]:
        """New updates, waiting up to `timeout` seconds for one to arrive (long polling).

        Passing `offset` confirms every update before it, and Telegram drops them.
        """
        payload: dict[str, object] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload)
        if not isinstance(result, list):
            raise _malformed("getUpdates")
        return [_update(item) for item in result]

    def send_message(self, chat_id: int, html: str) -> int:
        """Send an HTML message without a link preview. Returns its message id.

        `html` must already be escaped: Telegram rejects unknown tags with 400.
        """
        result = self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": html,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )
        return _field(result, "message_id", int, "sendMessage")

    def _call(self, method: str, payload: dict[str, object]) -> Any:
        try:
            response = self._http.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as exc:
            # `from None`: the httpx exception may quote the URL, token included.
            raise TelegramUnavailableError(method, type(exc).__name__) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise TelegramError(method, response.status_code, "response is not Bot API JSON")
        if body.get("ok") is True:
            return body.get("result")
        parameters = body.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        error_code = body.get("error_code")
        raise TelegramError(
            method,
            error_code if isinstance(error_code, int) else response.status_code,
            str(body.get("description", "")),
            retry_after if isinstance(retry_after, int) else None,
        )


def _update(item: object) -> Update:
    update_id = _field(item, "update_id", int, "getUpdates")
    return Update(update_id=update_id, message=_message(_get(item, "message")))


def _message(item: object) -> Message | None:
    chat = _get(item, "chat")
    chat_id, chat_type = _get(chat, "id"), _get(chat, "type")
    if not isinstance(chat_id, int) or not isinstance(chat_type, str):
        return None
    text = _get(item, "text")
    return Message(
        chat=Chat(id=chat_id, type=chat_type), text=text if isinstance(text, str) else None
    )


def _get(item: object, key: str) -> object:
    return item.get(key) if isinstance(item, dict) else None


def _field[T](item: object, key: str, kind: type[T], method: str) -> T:
    """`item[key]` if it is a `kind`. Anything else means the Bot API changed shape."""
    value = _get(item, key)
    if not isinstance(value, kind):
        raise _malformed(method)
    return value


def _malformed(method: str) -> TelegramError:
    return TelegramError(method, 0, "unexpected result shape")
