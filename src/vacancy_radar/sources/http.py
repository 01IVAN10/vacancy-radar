"""The HTTP client every adapter uses."""

import httpx

# The exact string agreed with DOU when it consented to collection: changing
# it, the version included, means telling DOU (see docs/sources.md).
USER_AGENT = "vacancy-radar/0.1 (+https://github.com/01IVAN10/vacancy-radar)"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def build_client() -> httpx.Client:
    """Client with the project User-Agent and timeouts that fail a hung fetch."""
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
