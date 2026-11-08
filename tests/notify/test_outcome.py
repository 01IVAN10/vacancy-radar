"""The failure policy of sending, one row of the table at a time. No database."""

import pytest

from vacancy_radar.notify.outcome import (
    BACKOFF_MAX_SECONDS,
    MAX_ATTEMPTS,
    OUTCOME_UNKNOWN,
    Outcome,
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

UNSENT = TelegramUnavailableError("sendMessage", "ConnectError", maybe_delivered=False)
MAYBE_SENT = TelegramUnavailableError("sendMessage", "ReadTimeout", maybe_delivered=True)
SERVER = TelegramServerError("sendMessage", 502, "Bad Gateway")


def test_429_waits_retry_after_without_counting_the_attempt() -> None:
    exc = TelegramRateLimitedError("sendMessage", 429, "Too Many Requests", retry_after=17)

    assert outcome_of(exc, attempts=1) == Outcome(
        "pending", str(exc), retry_in=17, count_attempt=False, stop_run=True
    )


@pytest.mark.parametrize("exc", [SERVER, UNSENT], ids=["5xx", "never sent"])
def test_a_failure_that_sent_nothing_is_retried_after_a_backoff(
    exc: Exception,
) -> None:
    outcome = outcome_of(exc, attempts=1)

    assert (outcome.status, outcome.count_attempt, outcome.stop_run) == ("pending", True, True)
    assert 1 <= outcome.retry_in <= BACKOFF_MAX_SECONDS
    assert not outcome.reraise


@pytest.mark.parametrize("exc", [SERVER, UNSENT], ids=["5xx", "never sent"])
def test_the_last_attempt_fails(exc: Exception) -> None:
    assert outcome_of(exc, attempts=MAX_ATTEMPTS) == Outcome("failed", str(exc), stop_run=True)


def test_a_request_that_may_have_arrived_is_never_repeated() -> None:
    assert outcome_of(MAYBE_SENT, attempts=1) == Outcome(
        "failed", f"{OUTCOME_UNKNOWN}: sendMessage: ReadTimeout"
    )


@pytest.mark.parametrize(
    "exc",
    [
        TelegramForbiddenError("sendMessage", 403, "Forbidden: bot was blocked by the user"),
        TelegramApiError("sendMessage", 400, "Bad Request: chat not found"),
    ],
    ids=["blocked", "chat not found"],
)
def test_a_chat_that_is_gone_fails_and_is_unlinked(exc: Exception) -> None:
    assert outcome_of(exc, attempts=1) == Outcome("failed", str(exc), unlink_chat=True)


def test_a_rejected_token_puts_the_delivery_back_and_raises() -> None:
    exc = TelegramUnauthorizedError("sendMessage", 401, "Unauthorized")

    assert outcome_of(exc, attempts=1) == Outcome(
        "pending", str(exc), count_attempt=False, stop_run=True, reraise=True
    )


@pytest.mark.parametrize(
    "exc",
    [
        TelegramApiError("sendMessage", 400, "Bad Request: can't parse entities"),
        TelegramConflictError("sendMessage", 409, "Conflict"),
        TelegramMalformedError("sendMessage", "HTTP 200 without Bot API JSON"),
    ],
)
def test_any_other_telegram_error_fails_the_delivery_only(exc: Exception) -> None:
    assert outcome_of(exc, attempts=1) == Outcome("failed", str(exc))


def test_an_error_of_ours_is_retried_and_raised() -> None:
    outcome = outcome_of(KeyError("oops"), attempts=1)

    assert (outcome.status, outcome.error, outcome.reraise) == ("pending", "KeyError: 'oops'", True)
    assert outcome_of(KeyError("oops"), attempts=MAX_ATTEMPTS).status == "failed"


@pytest.mark.parametrize("attempts", range(1, 10))
def test_the_backoff_stays_between_one_second_and_the_cap(attempts: int) -> None:
    assert 1 <= backoff_seconds(attempts) <= BACKOFF_MAX_SECONDS
