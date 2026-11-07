import json
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.telegram.client import (
    API_URL,
    TelegramClient,
    TelegramUnauthorizedError,
    build_http,
)
from vacancy_radar.telegram.polling import Sessions, confirm, poll_forever, poll_once

SITE = "https://radar.example"
TOKEN = "7000000001:AAtest-token"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=f"{API_URL}/bot{TOKEN}") as router:
        yield router


@pytest.fixture
def client() -> Iterator[TelegramClient]:
    with build_http() as http:
        yield TelegramClient(http, TOKEN)


@pytest.fixture
def sessions(db_session: Session) -> Sessions:
    """A session factory that hands out the test's session, which the fixture rolls back."""

    @contextmanager
    def shared() -> Iterator[Session]:
        yield db_session

    return shared


def fixture(name: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=fixture_bytes(f"telegram/{name}.json"))


def polls(route: respx.Route) -> list[dict[str, object]]:
    """The JSON body of every `getUpdates` call made so far."""
    return [json.loads(call.request.content) for call in route.calls]


class StopAfter:
    """A `pause` for `poll_forever` that records the delays and stops after `count`."""

    def __init__(self, stop: Event, count: int) -> None:
        self.stop, self.count, self.waits = stop, count, list[float]()

    def __call__(self, delay: float) -> None:
        self.waits.append(delay)
        if len(self.waits) == self.count:
            self.stop.set()


# --- poll_once ----------------------------------------------------------------------


def test_poll_once_answers_and_advances_the_offset(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    api.post("/getUpdates").mock(return_value=fixture("get_updates"))
    sent = api.post("/sendMessage").mock(return_value=fixture("send_message"))

    offset = poll_once(client, sessions, None, site_url=SITE)

    # The last update in the fixture is 500000004; the next poll confirms it.
    assert offset == 500000005
    # Only the private text message gets an answer: "/start TOKEN" with an unknown token.
    assert sent.call_count == 1
    assert b"expired or was already used" in sent.calls.last.request.content


def test_poll_once_without_updates_keeps_the_offset(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    api.post("/getUpdates").mock(return_value=fixture("get_updates_empty"))

    assert poll_once(client, sessions, 77, site_url=SITE) == 77


def test_a_failing_update_is_logged_and_still_confirmed(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    api.post("/getUpdates").mock(return_value=fixture("get_updates"))
    api.post("/sendMessage").mock(return_value=fixture("bot_blocked", 403))

    assert poll_once(client, sessions, None, site_url=SITE) == 500000005


# --- poll_forever -------------------------------------------------------------------


def test_stopping_confirms_the_last_batch(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    stop = Event()
    answered: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        answered.append(request)
        stop.set()  # SIGTERM arrives while the batch is answered
        return fixture("send_message")

    route = api.post("/getUpdates").mock(
        side_effect=[fixture("get_updates"), fixture("get_updates_empty")]
    )
    api.post("/sendMessage").mock(side_effect=answer)

    poll_forever(client, sessions, site_url=SITE, stop=stop)

    # Without the second call, Telegram would send the batch again at the next start.
    assert polls(route) == [
        {"timeout": 20, "allowed_updates": ["message"]},
        {"timeout": 0, "allowed_updates": ["message"], "offset": 500000005},
    ]


def test_stopping_before_any_update_confirms_nothing(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    stop = Event()
    stop.set()

    # No route is mocked: respx fails the test if any request is sent.
    poll_forever(client, sessions, site_url=SITE, stop=stop)

    assert api.calls.call_count == 0


def test_a_failed_confirmation_is_only_logged(
    api: respx.MockRouter, client: TelegramClient
) -> None:
    api.post("/getUpdates").mock(side_effect=httpx.ConnectError("down"))

    confirm(client, 500000005)  # does not raise


def test_poll_forever_backs_off_after_a_failed_poll_and_stops(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    stop = Event()
    pause = StopAfter(stop, 3)
    api.post("/getUpdates").mock(return_value=fixture("conflict", 409))

    poll_forever(client, sessions, site_url=SITE, stop=stop, pause=pause)

    assert pause.waits == [1, 2, 4]


def test_poll_forever_resets_the_backoff_after_a_good_poll(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    stop = Event()
    pause = StopAfter(stop, 2)
    api.post("/getUpdates").mock(
        side_effect=[
            fixture("conflict", 409),
            fixture("get_updates_empty"),
            fixture("conflict", 409),
        ]
    )

    poll_forever(client, sessions, site_url=SITE, stop=stop, pause=pause)

    assert pause.waits == [1, 1]


def test_a_rejected_token_ends_polling(
    api: respx.MockRouter, client: TelegramClient, sessions: Sessions
) -> None:
    # A revoked token is not retried every minute forever.
    api.post("/getUpdates").mock(return_value=fixture("unauthorized", 401))

    with pytest.raises(TelegramUnauthorizedError):
        poll_forever(client, sessions, site_url=SITE, stop=Event(), pause=lambda _: None)
