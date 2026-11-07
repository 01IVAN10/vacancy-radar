"""structlog setup shared by the CLI and, later, the API and workers."""

import logging
import sys
from typing import Final

import structlog

# httpx and httpcore log every request with its full URL at INFO, and the
# Telegram Bot API puts the bot token in the URL path. Celery attaches a
# handler to the root logger at the worker's level, so without this the
# token would reach the worker's log.
QUIET_LOGGERS: Final = ("httpx", "httpcore")


def configure_logging(level: str = "INFO", *, json: bool | None = None) -> None:
    """Log to stderr, as JSON when `json` is set or stderr is not a terminal.

    A terminal gets readable console output. CI logs and containers get one JSON
    object per line, which is easy to grep and to ship elsewhere later.
    """
    if json is None:
        json = not sys.stderr.isatty()
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info if json else structlog.dev.set_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        # Caching would make loggers created before configure() ignore it.
        cache_logger_on_first_use=False,
    )
