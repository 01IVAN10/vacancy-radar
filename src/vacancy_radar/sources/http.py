"""The HTTP client every adapter uses."""

import httpx

from vacancy_radar.ratelimit import TokenBucket, rate_limit_hooks

# The exact string agreed with DOU when it consented to collection: changing
# it, the version included, means telling DOU (see docs/sources.md).
USER_AGENT = "vacancy-radar/0.1 (+https://github.com/01IVAN10/vacancy-radar)"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def build_client(rate_limiter: TokenBucket | None = None) -> httpx.Client:
    """Client with the project User-Agent and timeouts that fail a hung fetch.

    With a `rate_limiter`, every request first waits for a token for its host.
    The Celery worker passes one. The CLI runs without Redis and without one.
    """
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        event_hooks=rate_limit_hooks(rate_limiter),
    )
