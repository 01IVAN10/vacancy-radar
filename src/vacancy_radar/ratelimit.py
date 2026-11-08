"""Token bucket rate limiter in Redis, one bucket per domain.

Every worker process shares the same buckets, so the limit holds across the
whole deployment, not per process. The collection rules in docs/sources.md
allow at most one request per second per source domain, and Telegram gets
the same one request a second (docs/telegram.md, Delivery).

`rate_limit_hook` applies a bucket to an httpx client, so every request waits
for a token for its host first. A failure of Redis itself is raised as
`RateLimitError` too: either way no token was taken and no request was sent.
"""

import time
from collections.abc import Callable
from functools import lru_cache

import httpx
from redis import Redis, RedisError

from vacancy_radar.config import get_settings

REQUESTS_PER_SECOND = 1.0
BURST = 1
# Longest a request waits for a token before giving up with `RateLimitError`.
MAX_WAIT_SECONDS = 10.0

KEY_PREFIX = "ratelimit:"

# Refill the bucket for the time passed since its last use, then take one token
# if there is one. Returns the seconds to wait for the next token (0 when one
# was taken) as a string, because Redis truncates Lua numbers to integers.
# Running it as one script makes read-refill-take atomic across workers.
_ACQUIRE = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at')
local tokens = tonumber(state[1]) or capacity
local updated_at = tonumber(state[2]) or now

tokens = math.min(capacity, tokens + math.max(0, now - updated_at) * rate)
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = (1 - tokens) / rate
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'updated_at', tostring(now))
-- A bucket left alone for long enough is full again, so it can expire.
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate * 1000) + 1000)
return tostring(wait)
"""


class RateLimitError(Exception):
    """No token was taken: none came free within the wait, or Redis failed. No request was sent."""


class TokenBucket:
    """Allows `rate` requests per second per key, with bursts of up to `capacity`.

    `clock` returns seconds since the epoch. Tests pass a fake one to control
    time; in production every caller runs on the same host, so clocks agree.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        rate: float = REQUESTS_PER_SECOND,
        capacity: int = BURST,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.redis = redis
        self.rate = rate
        self.capacity = capacity
        self.clock = clock
        self._acquire = redis.register_script(_ACQUIRE)

    def acquire(self, key: str) -> float:
        """Take a token for `key`. Returns 0, or the seconds until a token is free."""
        wait = self._acquire(
            keys=[KEY_PREFIX + key], args=[repr(self.rate), self.capacity, repr(self.clock())]
        )
        return float(wait)


def wait_for_token(
    bucket: TokenBucket,
    key: str,
    *,
    max_wait: float = MAX_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Block until a token for `key` is taken.

    Raises `RateLimitError` after `max_wait`, or when Redis fails.
    """
    waited = 0.0
    while (wait := _acquire(bucket, key)) > 0:
        if waited + wait > max_wait:
            raise RateLimitError(f"no request token for {key} within {max_wait:g} s")
        sleep(wait)
        waited += wait


def _acquire(bucket: TokenBucket, key: str) -> float:
    try:
        return bucket.acquire(key)
    except RedisError as exc:
        raise RateLimitError(f"no request token for {key}: {type(exc).__name__}") from exc


def rate_limit_hook(bucket: TokenBucket) -> Callable[[httpx.Request], None]:
    """httpx request hook that blocks until `bucket` allows a request to the host."""

    def hook(request: httpx.Request) -> None:
        wait_for_token(bucket, request.url.host)

    return hook


@lru_cache
def get_rate_limiter() -> TokenBucket:
    """Process-wide token bucket on the Celery Redis."""
    redis_url = get_settings().redis_url
    if redis_url is None:
        raise RuntimeError("REDIS_URL is required for the rate limiter")
    return TokenBucket(Redis.from_url(str(redis_url)))
