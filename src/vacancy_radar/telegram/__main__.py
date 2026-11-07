"""The bot process: `python -m vacancy_radar.telegram`. The loop is in `polling.py`.

Only one poller may run per bot: Telegram answers a second one with 409
Conflict. SIGTERM, which `docker compose stop` sends, ends the current long
poll, confirms the last batch and exits.

Exit codes are chosen for Compose's `restart: on-failure`:

- **0, stays down** when no restart can help: `TELEGRAM_BOT_TOKEN` is not
  set (logged as a warning), or Telegram rejects it, at start or later
  (`telegram.token_rejected`, an error). Restarting would only repeat the
  rejected request every few seconds; the fix is a new token and a restart.
- **1, restarted** when starting failed for another reason, such as no
  network yet. Compose restarts the process with its own growing delay.
"""

import signal
import sys
import threading

import structlog

from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging
from vacancy_radar.telegram.client import (
    TelegramClient,
    TelegramError,
    TelegramUnauthorizedError,
    build_http,
)
from vacancy_radar.telegram.polling import poll_forever

logger = structlog.get_logger("vacancy_radar.telegram")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.telegram_bot_token is None:
        logger.warning("telegram.not_configured", missing="TELEGRAM_BOT_TOKEN")
        return 0

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    with build_http() as http:
        client = TelegramClient(http, settings.telegram_bot_token.get_secret_value())
        try:
            username = client.get_me()
            if settings.telegram_bot_username not in (None, username):
                logger.warning(
                    "telegram.username_mismatch",
                    configured=settings.telegram_bot_username,
                    actual=username,
                )
            logger.info("telegram.polling", bot=username)
            poll_forever(client, get_sessionmaker(), site_url=settings.site_url, stop=stop)
        except TelegramUnauthorizedError as exc:
            logger.error("telegram.token_rejected", error=str(exc))
            return 0
        except TelegramError as exc:
            logger.error("telegram.start_failed", error=str(exc))
            return 1
    logger.info("telegram.stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
