"""The HTTP client every adapter uses."""

from collections.abc import Callable

import httpx

from vacancy_radar.sources.ratelimit import TokenBucket, wait_for_token

# The exact string agreed with DOU when it consented to collection: changing
# it, the version included, means telling DOU (see docs/sources.md).
USER_AGENT = "vacancy-radar/0.1 (+https://github.com/01IVAN10/vacancy-radar)"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def build_client(rate_limiter: TokenBucket | None = None) -> httpx.Client:
    """Client with the project User-Agent and timeouts that fail a hung fetch.

    With a `rate_limiter`, every request first waits for a token for its host.
    The Celery worker passes one. The CLI runs without Redis and without one.
    """
    hooks: dict[str, list[Callable[..., object]]] = {}
    if rate_limiter is not None:
        hooks["request"] = [rate_limit_hook(rate_limiter)]
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, event_hooks=hooks)


def rate_limit_hook(bucket: TokenBucket) -> Callable[[httpx.Request], None]:
    """httpx request hook that blocks until `bucket` allows a request to the host."""

    def hook(request: httpx.Request) -> None:
        wait_for_token(bucket, request.url.host)

    return hook
