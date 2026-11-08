"""The failure policy of sending, one row of the table at a time. No database."""

import pytest

from vacancy_radar.notify.outcome import (
    BACKOFF_MAX_SECONDS,
    MAX_ATTEMPTS,
    OUTCOME_UNKNOWN,
    GiveUp,
    Outcome,
    Retry,
    backoff_seconds,
    outcome_of,
)
from vacancy_radar.telegram.client import (
    TelegramApiError,
    TelegramConflictError,
    TelegramForbiddenError,
    TelegramMalformedError,
    TelegramRateLimitedError,
    TelegramServerError,
    TelegramUnauthorizedError,
    TelegramUnavailableError,
)

NEVER_SENT = TelegramUnavailableError("sendMessage", "ConnectError", maybe_delivered=False)


def test_429_waits_retry_after_without_counting_the_attempt() -> None:
    exc = TelegramRateLimitedError("sendMessage", 429, "Too Many Requests", retry_after=17)

    assert outcome_of(exc, attempts=1) == Outcome(Retry(17, count_attempt=False), "stop", str(exc))


def test_a_request_that_never_left_is_retried_after_a_backoff() -> None:
    outcome = outcome_of(NEVER_SENT, attempts=1)

    assert isinstance(outcome.delivery, Retry)
    assert outcome.delivery.count_attempt
    assert 1 <= outcome.delivery.after <= BACKOFF_MAX_SECONDS
    assert outcome.run == "stop"


def test_the_last_attempt_gives_up() -> None:
    assert outcome_of(NEVER_SENT, attempts=MAX_ATTEMPTS) == Outcome(
        GiveUp(), "stop", "sendMessage: ConnectError"
    )


@pytest.mark.parametrize(
    "exc",
    [
        TelegramUnavailableError("sendMessage", "ReadTimeout", maybe_delivered=True),
        TelegramServerError("sendMessage", 502, "HTTP 502 without Bot API JSON"),
        TelegramServerError("sendMessage", 500, "Internal Server Error"),
    ],
    ids=["read timeout", "gateway 502", "bot api 500"],
)
def test_a_request_that_may_have_arrived_is_never_repeated(exc: Exception) -> None:
    # A 5xx too: a gateway may answer with one after the message went out.
    assert outcome_of(exc, attempts=1) == Outcome(GiveUp(), "stop", f"{OUTCOME_UNKNOWN}: {exc}")


@pytest.mark.parametrize(
    "exc",
    [
        TelegramForbiddenError("sendMessage", 403, "Forbidden: bot was blocked by the user"),
        TelegramApiError("sendMessage", 400, "Bad Request: chat not found"),
    ],
    ids=["blocked", "chat not found"],
)
def test_a_chat_that_is_gone_gives_up_and_unlinks(exc: Exception) -> None:
    assert outcome_of(exc, attempts=1) == Outcome(GiveUp(unlink_chat=True), "continue", str(exc))


def test_a_rejected_token_puts_the_delivery_back_and_raises() -> None:
    exc = TelegramUnauthorizedError("sendMessage", 401, "Unauthorized")

    assert outcome_of(exc, attempts=1) == Outcome(Retry(0, count_attempt=False), "raise", str(exc))


@pytest.mark.parametrize(
    "exc",
    [
        TelegramApiError("sendMessage", 400, "Bad Request: can't parse entities"),
        TelegramConflictError("sendMessage", 409, "Conflict"),
        TelegramMalformedError("sendMessage", "HTTP 200 without Bot API JSON"),
    ],
)
def test_any_other_telegram_error_gives_up_on_that_delivery_only(exc: Exception) -> None:
    assert outcome_of(exc, attempts=1) == Outcome(GiveUp(), "continue", str(exc))


def test_an_error_of_ours_is_retried_and_raised() -> None:
    outcome = outcome_of(KeyError("oops"), attempts=1)

    assert isinstance(outcome.delivery, Retry)
    assert (outcome.run, outcome.error) == ("raise", "KeyError: 'oops'")
    assert outcome_of(KeyError("oops"), attempts=MAX_ATTEMPTS).delivery == GiveUp()


@pytest.mark.parametrize("attempts", range(1, 10))
def test_the_backoff_stays_between_one_second_and_the_cap(attempts: int) -> None:
    assert 1 <= backoff_seconds(attempts) <= BACKOFF_MAX_SECONDS
