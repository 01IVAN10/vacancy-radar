"""Exponential backoff: how long to wait before the next retry.

Callers bring their own policy, the base delay and the cap; this module only
does the arithmetic. With full jitter the delay is random, so failures that
happened together are not retried together.
"""

import random


def exponential_backoff(retries: int, *, base: int, cap: int, jitter: bool) -> int:
    """Seconds to wait after `retries` earlier retries: `base * 2**retries`, at most `cap`.

    With `jitter`, a random whole number from 0 to that value inclusive
    (full jitter).
    """
    if retries < 0:
        raise ValueError(f"retries must not be negative, got {retries}")
    # Annotated: typeshed types `int ** int` as Any when the exponent may be negative.
    ceiling: int = min(cap, base * 2**retries)
    if not jitter:
        return ceiling
    return random.randint(0, ceiling)  # noqa: S311 - jitter spreads retries, it is not a secret
