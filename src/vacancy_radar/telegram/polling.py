"""Long polling: fetch updates with `getUpdates` and answer each with `handle_update`.

The offset of the next update is kept in memory. Polling with it confirms
every earlier update, and Telegram keeps unconfirmed ones for 24 hours.
So when polling stops, one last `getUpdates` with the offset and no wait
confirms the batch just answered; otherwise the next start would answer it
again, and a `/start` would get "this link was already used" after "linked".

Only a process that is killed, rather than stopped, leaves its last batch
unconfirmed, and that batch is answered again at the next start.
"""

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy.orm import Session

from vacancy_radar.telegram.bot import handle_update
from vacancy_radar.telegram.client import (
    TelegramClient,
    TelegramConflictError,
    TelegramError,
    TelegramUnauthorizedError,
)

logger = structlog.get_logger("vacancy_radar.telegram")

# Seconds Telegram holds a `getUpdates` request open waiting for an update.
POLL_TIMEOUT: Final = 20
# Pauses after failed polls double from 1 s up to this cap.
MAX_BACKOFF_SECONDS: Final = 60

# Opens a session for one unit of work: `with sessions() as session`.
Sessions = Callable[[], AbstractContextManager[Session]]


def poll_once(
    client: TelegramClient, sessions: Sessions, offset: int | None, *, site_url: str
) -> int | None:
    """Answer one batch of updates. Returns the offset for the next poll.

    A failure while answering one update, whatever it is, is logged and the
    update is still confirmed. Otherwise a message that always fails, or a
    database outage, would come back after every restart and stop the bot.
    The sender of a lost `/start` sees no reply and can press Start again.
    """
    for update in client.get_updates(offset=offset, timeout=POLL_TIMEOUT):
        offset = update.update_id + 1
        try:
            with sessions() as session:
                reply = handle_update(session, update, now=datetime.now(UTC), site_url=site_url)
            if reply is not None and update.message is not None:
                client.send_message(update.message.chat.id, reply)
        except Exception:
            logger.exception("telegram.update_failed", update_id=update.update_id)
    return offset


def poll_forever(
    client: TelegramClient,
    sessions: Sessions,
    *,
    site_url: str,
    stop: threading.Event,
    pause: Callable[[float], object] | None = None,
) -> None:
    """Poll until `stop` is set, then confirm the last batch.

    Failed polls are retried after a pause with exponential backoff.
    `pause` waits; by default it is `stop.wait`, so a stop request ends a
    pause at once. Tests pass their own. A rejected token
    (`TelegramUnauthorizedError`) is raised: no retry fixes it.
    """
    pause = pause or stop.wait
    offset: int | None = None
    failures = 0
    while not stop.is_set():
        try:
            offset = poll_once(client, sessions, offset, site_url=site_url)
        except TelegramUnauthorizedError:
            raise
        except TelegramError as exc:
            failures += 1
            delay = min(MAX_BACKOFF_SECONDS, 2 ** (failures - 1))
            if isinstance(exc, TelegramConflictError):
                logger.error("telegram.poll_conflict", error=str(exc), retry_in=delay)
            elif exc.retryable:
                # A timeout or a 5xx comes and goes; only a lasting one needs a look.
                logger.warning("telegram.poll_failed", error=str(exc), retry_in=delay)
            else:
                logger.error("telegram.poll_failed", error=str(exc), retry_in=delay)
            pause(delay)
        else:
            failures = 0
    confirm(client, offset)


def confirm(client: TelegramClient, offset: int | None) -> None:
    """Confirm every update before `offset` without waiting for new ones."""
    if offset is None:
        return
    try:
        client.get_updates(offset=offset, timeout=0)
    except TelegramError as exc:
        # The batch will be answered again at the next start.
        logger.warning("telegram.confirm_failed", offset=offset, error=str(exc))
