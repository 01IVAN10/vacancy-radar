"""What a failed send means for its delivery and for the run. Pure: no I/O.

`send_pending` claims a delivery, tries to send it, and on an exception asks
`outcome_of` what to do. The whole policy is this one table:

| Failure | Delivery | Attempt counted | Run |
|---|---|---|---|
| 429 | retried after `retry_after` | no | stops |
| request never sent (no connection, no token) | retried after a backoff | yes | stops |
| 5xx, or a timeout after the request was sent | failed, "outcome unknown" | yes | stops |
| 403; 400 "chat not found" | failed; chat unlinked | yes | goes on |
| 401: token rejected | retried at once | no | raises |
| any other Telegram error | failed | yes | goes on |
| our own error before the request | retried after a backoff | yes | raises |

A backoff turns into a failure after `MAX_ATTEMPTS`. A request that may have
reached Telegram is never repeated: a missed message is better than a
duplicate. A 5xx counts as such, because a gateway in front of the Bot API
may answer with one after the message went out. During a Telegram outage
each run therefore gives up one delivery before it stops.
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
class Retry:
    """Put the delivery back as `pending`, due in `after` seconds."""

    after: int
    # False gives back the attempt the claim counted.
    count_attempt: bool = True


@dataclass(frozen=True, slots=True)
class GiveUp:
    """Mark the delivery `failed`."""

    unlink_chat: bool = False


# continue: go on with the next delivery; stop: end the run, because Telegram
# or the network fails for every delivery; raise: end it with the exception.
RunEffect = Literal["continue", "stop", "raise"]


@dataclass(frozen=True, slots=True)
class Outcome:
    delivery: Retry | GiveUp
    run: RunEffect
    error: str


def outcome_of(exc: Exception, *, attempts: int) -> Outcome:
    """The outcome of a send that raised `exc`, after `attempts` attempts including this one."""
    error = str(exc) or type(exc).__name__
    match exc:
        case TelegramRateLimitedError():
            return Outcome(Retry(exc.retry_after, count_attempt=False), "stop", error)
        case TelegramUnauthorizedError():
            return Outcome(Retry(0, count_attempt=False), "raise", error)
        case TelegramUnavailableError() if not exc.maybe_delivered:
            return _retry_later(attempts, "stop", error)
        case TelegramUnavailableError() | TelegramServerError():
            return Outcome(GiveUp(), "stop", f"{OUTCOME_UNKNOWN}: {error}")
        case TelegramForbiddenError():
            return Outcome(GiveUp(unlink_chat=True), "continue", error)
        case TelegramApiError() if CHAT_NOT_FOUND in exc.description.lower():
            return Outcome(GiveUp(unlink_chat=True), "continue", error)
        case TelegramError():
            return Outcome(GiveUp(), "continue", error)
        case _:
            # Not from the Bot API: raised before the request was sent.
            return _retry_later(attempts, "raise", f"{type(exc).__name__}: {error}")


def _retry_later(attempts: int, run: RunEffect, error: str) -> Outcome:
    if attempts >= MAX_ATTEMPTS:
        return Outcome(GiveUp(), run, error)
    return Outcome(Retry(backoff_seconds(attempts)), run, error)


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
