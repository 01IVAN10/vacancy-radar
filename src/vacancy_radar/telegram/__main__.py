"""The bot process: `python -m vacancy_radar.telegram`.

It long-polls the Bot API (`getUpdates`) and answers each message with
`handle_update`. Polling needs no public HTTPS endpoint, so the same
process runs locally and on the VPS. Only one poller may run per bot:
Telegram answers a second one with 409 Conflict.

The offset of the next update is kept in memory. Asking with it confirms
every earlier update, so after a restart Telegram sends again only the
updates of the last, unconfirmed batch. A repeated `/start` then finds its
token used and says so, which is harmless.

Without `TELEGRAM_BOT_TOKEN` the process logs a warning and exits with 0,
so the Compose service stays down instead of restarting forever.
"""

import signal
import sys
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy.orm import Session

from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging
from vacancy_radar.sources.http import build_client
from vacancy_radar.telegram.bot import handle_update
from vacancy_radar.telegram.client import (
    TelegramClient,
    TelegramError,
    TelegramUnavailableError,
)

logger = structlog.get_logger("vacancy_radar.telegram")

# Seconds Telegram holds a `getUpdates` request open waiting for an update.
# Below the read timeout of `build_client` (30 s), so a quiet poll is not a timeout.
POLL_TIMEOUT: Final = 20
# Pauses after failed polls double from 1 s up to this cap.
MAX_BACKOFF_SECONDS: Final = 60

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
    """Poll until `stop` is set, pausing with exponential backoff after failures.

    `pause` waits between failed polls; by default it is `stop.wait`, so a
    stop request ends a pause at once. Tests pass their own.
    """
    pause = pause or stop.wait
    offset: int | None = None
    failures = 0
    while not stop.is_set():
        try:
            offset = poll_once(client, sessions, offset, site_url=site_url)
        except (TelegramError, TelegramUnavailableError) as exc:
            failures += 1
            delay = min(MAX_BACKOFF_SECONDS, 2 ** (failures - 1))
            conflict = isinstance(exc, TelegramError) and exc.error_code == 409
            logger.error(
                "telegram.poll_conflict" if conflict else "telegram.poll_failed",
                error=str(exc),
                retry_in=delay,
            )
            pause(delay)
        else:
            failures = 0


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.telegram_bot_token is None:
        logger.warning("telegram.not_configured", missing="TELEGRAM_BOT_TOKEN")
        return 0

    stop = threading.Event()
    # Docker stops a container with SIGTERM; finish the current poll and exit.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    with build_client() as http:
        client = TelegramClient(http, settings.telegram_bot_token.get_secret_value())
        try:
            username = client.get_me()
        except (TelegramError, TelegramUnavailableError) as exc:
            logger.error("telegram.start_failed", error=str(exc))
            return 1
        if settings.telegram_bot_username not in (None, username):
            logger.warning(
                "telegram.username_mismatch",
                configured=settings.telegram_bot_username,
                actual=username,
            )
        logger.info("telegram.polling", bot=username)
        poll_forever(client, get_sessionmaker(), site_url=settings.site_url, stop=stop)
    logger.info("telegram.stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
