"""A small Telegram Bot API client on plain httpx, without a bot framework.

Only the three methods the project uses are wrapped: `getMe`, `getUpdates`
and `sendMessage`. Responses are parsed into typed dataclasses here, so the
rest of the code never handles raw JSON.

**Errors are classified once, here.** Every failure is a `TelegramError`,
and its subclass says what the caller should do; `retryable` is true for the
transient ones:

| Error | When | Retryable |
|---|---|---|
| `TelegramUnavailableError` | no answer: a timeout or a network error | yes (see below) |
| `TelegramServerError` | 5xx, from Telegram or a proxy in front of it | yes |
| `TelegramRateLimitedError` | 429; wait `retry_after` seconds | yes |
| `TelegramUnauthorizedError` | 401: the token is wrong or was revoked | no |
| `TelegramForbiddenError` | 403: the user blocked the bot, or the chat is gone | no |
| `TelegramConflictError` | 409: another process polls the same bot | no |
| `TelegramApiError` | any other `ok: false` answer, such as 400 | no |
| `TelegramMalformedError` | an answer that is not shaped like the Bot API | no |

Retrying a read is always safe, but retrying a `sendMessage` is not: when the
connection fails after the request was sent, Telegram may have delivered the
message. `TelegramUnavailableError.maybe_delivered` is False only when the
request certainly never left (no connection, or no rate-limit token).

**The bot token never reaches a log or an error message.** It is part of
every request URL (`/bot<token>/<method>`). httpx's own request log is
silenced in `log.py`, and every error raised here names the method only: a
transport error is replaced without the httpx exception chained, because
some httpx errors quote the URL.
"""

from dataclasses import dataclass
from typing import Any, Final

import httpx

from vacancy_radar import __version__
from vacancy_radar.ratelimit import RateLimitError, TokenBucket, rate_limit_hooks

API_URL: Final = "https://api.telegram.org"

# Any Bot API call but a long poll answers within seconds.
TIMEOUT: Final = httpx.Timeout(10.0)
# A long poll holds the request open for its `timeout`; the read may take that
# long plus this margin before it counts as hung.
LONG_POLL_MARGIN_SECONDS: Final = 10
# Telegram always says how long to wait after 429. Should it ever not, wait this long.
DEFAULT_RETRY_AFTER_SECONDS: Final = 5


# Transport errors raised before any byte of the request was sent.
_NOT_SENT: Final = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def build_http(rate_limiter: TokenBucket | None = None) -> httpx.Client:
    """The HTTP client for the Bot API: the project's name and short timeouts.

    Separate from the source adapters' client, whose User-Agent and timeouts
    are agreed with the sources. With a `rate_limiter`, every request first
    waits for a token for `api.telegram.org`, as the worker's sends do.
    """
    return httpx.Client(
        headers={"User-Agent": f"vacancy-radar/{__version__}"},
        timeout=TIMEOUT,
        event_hooks=rate_limit_hooks(rate_limiter),
    )


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
    """A Bot API call failed. Subclasses say how; see the module docstring."""

    retryable = False

    def __init__(self, method: str, reason: str) -> None:
        super().__init__(f"{method}: {reason}")
        self.method = method


class TelegramUnavailableError(TelegramError):
    """No answer from the Bot API: a timeout or a network error.

    `maybe_delivered` is True when the request may have reached Telegram
    before the connection failed, such as on a read timeout. A message sent
    then may have been delivered, and sending it again may duplicate it.
    """

    retryable = True

    def __init__(self, method: str, reason: str, *, maybe_delivered: bool) -> None:
        super().__init__(method, reason)
        self.maybe_delivered = maybe_delivered


class TelegramMalformedError(TelegramError):
    """An answer that is not shaped like the Bot API's, such as an HTML error page."""


class TelegramApiError(TelegramError):
    """The Bot API answered `ok: false`. `error_code` follows HTTP status codes."""

    def __init__(self, method: str, error_code: int, description: str) -> None:
        super().__init__(method, f"{error_code} {description}")
        self.error_code = error_code
        self.description = description


class TelegramServerError(TelegramApiError):
    retryable = True


class TelegramRateLimitedError(TelegramApiError):
    retryable = True

    def __init__(self, method: str, error_code: int, description: str, retry_after: int) -> None:
        super().__init__(method, error_code, description)
        self.retry_after = retry_after


class TelegramUnauthorizedError(TelegramApiError):
    pass


class TelegramForbiddenError(TelegramApiError):
    pass


class TelegramConflictError(TelegramApiError):
    pass


_BY_CODE: Final[dict[int, type[TelegramApiError]]] = {
    401: TelegramUnauthorizedError,
    403: TelegramForbiddenError,
    409: TelegramConflictError,
}


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

        Passing `offset` confirms every update before it, and Telegram drops
        them. The request's read timeout is set from `timeout` here, so a
        quiet poll never times out whatever the client's own timeout is.
        """
        payload: dict[str, object] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call(
            "getUpdates",
            payload,
            timeout=httpx.Timeout(timeout + LONG_POLL_MARGIN_SECONDS, connect=TIMEOUT.connect),
        )
        if not isinstance(result, list):
            raise TelegramMalformedError("getUpdates", "the result is not a list")
        # An update without an id cannot be confirmed or answered; Telegram
        # does not send those, but one must not hide the rest of the batch.
        return [update for item in result if (update := _update(item)) is not None]

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

    def _call(
        self, method: str, payload: dict[str, object], *, timeout: httpx.Timeout | None = None
    ) -> Any:
        try:
            response = self._http.post(
                f"{self._base}/{method}",
                json=payload,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except RateLimitError:
            raise TelegramUnavailableError(
                method, "RateLimitError", maybe_delivered=False
            ) from None
        except httpx.HTTPError as exc:
            # `from None`: the httpx exception may quote the URL, token included.
            raise TelegramUnavailableError(
                method, type(exc).__name__, maybe_delivered=not isinstance(exc, _NOT_SENT)
            ) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            reason = f"HTTP {response.status_code} without Bot API JSON"
            if response.is_server_error:
                raise TelegramServerError(method, response.status_code, reason)
            raise TelegramMalformedError(method, reason)
        if body.get("ok") is True:
            return body.get("result")
        raise _api_error(method, body, response.status_code)


def _api_error(method: str, body: dict[str, Any], status_code: int) -> TelegramApiError:
    error_code = body.get("error_code")
    code = error_code if isinstance(error_code, int) else status_code
    description = str(body.get("description", ""))
    if code == 429:
        parameters = body.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        return TelegramRateLimitedError(
            method,
            code,
            description,
            retry_after if isinstance(retry_after, int) else DEFAULT_RETRY_AFTER_SECONDS,
        )
    if code >= 500:
        return TelegramServerError(method, code, description)
    return _BY_CODE.get(code, TelegramApiError)(method, code, description)


def _update(item: object) -> Update | None:
    update_id = _get(item, "update_id")
    if not isinstance(update_id, int):
        return None
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
        raise TelegramMalformedError(method, f"the result has no {key}")
    return value
