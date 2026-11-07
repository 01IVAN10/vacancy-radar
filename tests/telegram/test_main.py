import signal
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
import structlog

from tests.support import fixture_bytes
from vacancy_radar.config import get_settings
from vacancy_radar.telegram import __main__ as bot_main
from vacancy_radar.telegram.client import API_URL

TOKEN = "7000000001:AAtest-token"


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Fresh settings per test, and no real signal handlers in the test process."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "vacancy_radar_test_bot")
    # Set explicitly: a developer's `.env` may name another site.
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://radar.example/")
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()
    structlog.reset_defaults()


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=f"{API_URL}/bot{TOKEN}") as router:
        yield router


@pytest.fixture
def polled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the endless loop with a record of how it would have been started."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(bot_main, "poll_forever", lambda *args, **kwargs: calls.append(kwargs))
    return calls


def fixture(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=fixture_bytes(f"telegram/{name}.json"))


def test_without_a_token_the_bot_does_not_start(
    settings: pytest.MonkeyPatch, polled: list[dict[str, Any]]
) -> None:
    settings.setenv("TELEGRAM_BOT_TOKEN", "")

    assert bot_main.main() == 0
    assert polled == []


def test_a_rejected_token_fails_at_start(
    api: respx.MockRouter, polled: list[dict[str, Any]]
) -> None:
    api.post("/getMe").mock(return_value=fixture("unauthorized", 401))

    assert bot_main.main() == 1
    assert polled == []


def test_a_valid_token_starts_polling(api: respx.MockRouter, polled: list[dict[str, Any]]) -> None:
    api.post("/getMe").mock(return_value=fixture("get_me"))

    assert bot_main.main() == 0
    [started] = polled
    assert started["site_url"] == "https://radar.example"
    assert not started["stop"].is_set()


def test_a_different_configured_username_still_starts(
    settings: pytest.MonkeyPatch, api: respx.MockRouter, polled: list[dict[str, Any]]
) -> None:
    # Only logged: the deep links would open another bot, which the log says.
    settings.setenv("TELEGRAM_BOT_USERNAME", "some_other_bot")
    api.post("/getMe").mock(return_value=fixture("get_me"))

    assert bot_main.main() == 0
    assert len(polled) == 1
