"""Token bucket rate limiter in Redis, one bucket per source domain.

Every worker process shares the same buckets, so the limit holds across the
whole deployment, not per process. The collection rules in docs/sources.md
allow at most one request per second per domain.
"""

import time
from collections.abc import Callable

from redis import Redis

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
    """No token became available within the allowed wait. No request was sent."""


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
    """Block until a token for `key` is taken. Raises `RateLimitError` after `max_wait`."""
    waited = 0.0
    while (wait := bucket.acquire(key)) > 0:
        if waited + wait > max_wait:
            raise RateLimitError(f"no request token for {key} within {max_wait:g} s")
        sleep(wait)
        waited += wait
