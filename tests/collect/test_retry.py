import httpx
import pytest

from vacancy_radar.collect.retry import (
    BACKOFF_FACTOR_SECONDS,
    BACKOFF_MAX_SECONDS,
    MIN_DELAY_SECONDS,
    is_blocked,
    is_retryable,
    retry_after_seconds,
    retry_delay,
)
from vacancy_radar.sources.base import FeedError
from vacancy_radar.sources.ratelimit import RateLimitError

REQUEST = httpx.Request("GET", "https://djinni.co/jobs/rss/")


def status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    response = httpx.Response(status, headers=headers, request=REQUEST)
    return httpx.HTTPStatusError(f"HTTP {status}", request=REQUEST, response=response)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused", request=REQUEST),
        httpx.ReadTimeout("timed out", request=REQUEST),
        httpx.RemoteProtocolError("server disconnected", request=REQUEST),
        status_error(500),
        status_error(503),
        RateLimitError("no token"),
    ],
    ids=lambda exc: type(exc).__name__,
)
def test_transient_failures_are_retried(exc: Exception) -> None:
    assert is_retryable(exc)
    assert retry_delay(exc, retries=0) is not None


@pytest.mark.parametrize(
    "exc",
    [status_error(403), status_error(404), status_error(429), FeedError("not RSS"), ValueError()],
    ids=["403", "404", "429", "FeedError", "ValueError"],
)
def test_other_failures_are_not_retried(exc: Exception) -> None:
    assert not is_retryable(exc)
    assert retry_delay(exc, retries=0) is None


@pytest.mark.parametrize(
    ("status", "blocked"), [(403, True), (429, True), (404, False), (503, False)]
)
def test_403_and_429_mean_blocked(status: int, blocked: bool) -> None:
    assert is_blocked(status_error(status)) is blocked


def test_not_a_status_error_is_not_blocked() -> None:
    assert not is_blocked(httpx.ConnectError("refused", request=REQUEST))


@pytest.mark.parametrize("retries", range(8))
def test_delay_grows_exponentially_with_jitter_up_to_the_cap(retries: int) -> None:
    ceiling = max(MIN_DELAY_SECONDS, min(BACKOFF_MAX_SECONDS, BACKOFF_FACTOR_SECONDS * 2**retries))

    delays = {retry_delay(status_error(503), retries) for _ in range(200)}

    assert all(d is not None and MIN_DELAY_SECONDS <= d <= ceiling for d in delays)
    # Full jitter: the delays are spread out, not one fixed value.
    assert len(delays) > 1


def test_retry_after_is_honoured() -> None:
    delays = {retry_delay(status_error(503, {"Retry-After": "120"}), 0) for _ in range(50)}

    assert delays == {120}


def test_retry_after_beyond_the_cap_leaves_it_to_the_next_scheduled_run() -> None:
    exc = status_error(503, {"Retry-After": str(BACKOFF_MAX_SECONDS + 1)})

    assert retry_delay(exc, retries=0) is None


@pytest.mark.parametrize(
    ("headers", "seconds"),
    [
        ({"Retry-After": "30"}, 30),
        ({"Retry-After": " 7 "}, 7),
        ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
        ({}, None),
    ],
)
def test_retry_after_is_read_only_as_seconds(headers: dict[str, str], seconds: int | None) -> None:
    assert retry_after_seconds(status_error(503, headers)) == seconds
