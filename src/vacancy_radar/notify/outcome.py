"""What a failed send means for its delivery. Pure: no I/O.

`send_pending` claims a delivery, tries to send it, and on an exception asks
`outcome_of` what to do. The whole policy is this one table:

| Failure | Delivery | Attempt counted | Run |
|---|---|---|---|
| 429 | `pending` after `retry_after` | no | stops |
| 5xx; request never sent | `pending` after a backoff | yes | stops |
| timeout after the request was sent | `failed`, "outcome unknown" | yes | goes on |
| 403; 400 "chat not found" | `failed`; chat unlinked | yes | goes on |
| 401: token rejected | `pending` at once | no | raises |
| any other Telegram error | `failed` | yes | goes on |
| our own error before the request | `pending` after a backoff | yes | raises |

A backoff turns into `failed` after `MAX_ATTEMPTS`. A request that may have
reached Telegram is never repeated: a missed message is better than a duplicate.
"""

from dataclasses import dataclass
from typing import Final, Literal

from celery.utils.time import get_exponential_backoff_interval

from vacancy_radar.telegram.client import (
    TelegramApiError,
    TelegramError,
    TelegramForbiddenError,
    TelegramRateLimitedError,
    TelegramServerError,
    TelegramUnauthorizedError,
    TelegramUnavailableError,
)

MAX_ATTEMPTS: Final = 5
# Backoff doubles from 30 s, capped at 10 min, with full jitter.
BACKOFF_FACTOR_SECONDS: Final = 30
BACKOFF_MAX_SECONDS: Final = 600

OUTCOME_UNKNOWN: Final = "outcome unknown"
# Bot API's description when a chat no longer exists; it comes with a 400.
CHAT_NOT_FOUND: Final = "chat not found"


@dataclass(frozen=True, slots=True)
class Outcome:
    """What becomes of a claimed delivery after a failed send."""

    status: Literal["pending", "failed"]
    error: str
    # For `pending`: seconds until the next attempt.
    retry_in: int = 0
    # False puts back the attempt the claim counted.
    count_attempt: bool = True
    # Stop the run: Telegram, or something of ours, is failing for every delivery.
    stop_run: bool = False
    # Re-raise the exception once the delivery is stored.
    reraise: bool = False
    unlink_chat: bool = False


def outcome_of(exc: Exception, *, attempts: int) -> Outcome:
    """The outcome of a send that raised `exc`, after `attempts` attempts including this one."""
    error = str(exc) or type(exc).__name__
    match exc:
        case TelegramRateLimitedError():
            return Outcome(
                "pending", error, retry_in=exc.retry_after, count_attempt=False, stop_run=True
            )
        case TelegramUnauthorizedError():
            return Outcome("pending", error, count_attempt=False, stop_run=True, reraise=True)
        case TelegramUnavailableError() if exc.maybe_delivered:
            return Outcome("failed", f"{OUTCOME_UNKNOWN}: {error}")
        case TelegramServerError() | TelegramUnavailableError():
            return _retry_later(error, attempts, stop_run=True)
        case TelegramForbiddenError():
            return Outcome("failed", error, unlink_chat=True)
        case TelegramApiError() if CHAT_NOT_FOUND in exc.description.lower():
            return Outcome("failed", error, unlink_chat=True)
        case TelegramError():
            return Outcome("failed", error)
        case _:
            # Not from the Bot API: raised before the request was sent.
            return _retry_later(f"{type(exc).__name__}: {error}", attempts, reraise=True)


def _retry_later(
    error: str, attempts: int, *, stop_run: bool = True, reraise: bool = False
) -> Outcome:
    if attempts >= MAX_ATTEMPTS:
        return Outcome("failed", error, stop_run=stop_run, reraise=reraise)
    return Outcome(
        "pending", error, retry_in=backoff_seconds(attempts), stop_run=stop_run, reraise=reraise
    )


def backoff_seconds(attempts: int) -> int:
    """Delay after `attempts` failed attempts: full jitter below the doubling cap, at least 1 s."""
    return max(
        1,
        get_exponential_backoff_interval(
            factor=BACKOFF_FACTOR_SECONDS,
            retries=attempts - 1,
            maximum=BACKOFF_MAX_SECONDS,
            full_jitter=True,
        ),
    )
