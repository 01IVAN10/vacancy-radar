"""Which failed requests are worth retrying, and when. Pure: no I/O.

Timeouts, network errors and 5xx are usually over within minutes, so they are
retried with exponential backoff and full jitter. 403 and 429 mean the source
does not want our requests right now: the collection rules (docs/sources.md)
say to stop and alert instead of retrying. Other 4xx and unparsable feeds do
not fix themselves on a retry.
"""

import httpx
from celery.utils.time import get_exponential_backoff_interval

from vacancy_radar.ratelimit import RateLimitError

# Backoff doubles from 30 s, capped at 10 min, and full jitter picks a random
# delay below it. Retries stay well inside Redis's visibility timeout (1 h),
# past which the broker would deliver a delayed task a second time.
BACKOFF_FACTOR_SECONDS = 30
BACKOFF_MAX_SECONDS = 600
MIN_DELAY_SECONDS = 5

BLOCKED_STATUSES = frozenset({403, 429})


def is_blocked(exc: BaseException) -> bool:
    """The source refused us (403/429): stop and alert, do not retry."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in BLOCKED_STATUSES


def is_retryable(exc: BaseException) -> bool:
    """A transient failure: a timeout, a network error, a 5xx, or no rate-limit token."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.is_server_error
    return isinstance(
        exc,
        httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError | RateLimitError,
    )


def retry_delay(exc: BaseException, retries: int) -> int | None:
    """Seconds to wait before retry number `retries + 1`, or None not to retry.

    A numeric `Retry-After` is honoured. If it asks for longer than the backoff
    cap, the request is not retried: the next scheduled run picks it up.
    """
    if not is_retryable(exc):
        return None

    backoff = get_exponential_backoff_interval(
        factor=BACKOFF_FACTOR_SECONDS,
        retries=retries,
        maximum=BACKOFF_MAX_SECONDS,
        full_jitter=True,
    )
    delay = max(MIN_DELAY_SECONDS, backoff)

    retry_after = retry_after_seconds(exc)
    if retry_after is None:
        return delay
    if retry_after > BACKOFF_MAX_SECONDS:
        return None
    return max(delay, retry_after)


def retry_after_seconds(exc: BaseException) -> int | None:
    """The `Retry-After` of a failed response, if it is a number of seconds.

    The HTTP-date form is ignored. Neither source has been seen sending it.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    value = exc.response.headers.get("Retry-After", "").strip()
    return int(value) if value.isdigit() else None
