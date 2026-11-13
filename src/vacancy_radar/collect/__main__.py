"""Command line entry point: `python -m vacancy_radar.collect [SOURCE ...]`.

Each source is collected on its own. A failing source, whatever the error, is
recorded and logged and the rest still run; the exit code is 1 if any source failed, so a cron
job shows up red.

The schedule runs more often than any source may be fetched. A source is
skipped when it is listed in `DISABLED_SOURCES`, when another collector is
starting a run for it right now, or when its `min_interval` has not passed
since its last request (`collect_run`). The checks live in `collect_if_due`, shared
with the Celery task.

A source that becomes due within `MAX_WAIT` is waited for instead of skipped.
The job starts every 15 minutes, and a start that comes a few seconds short of
DOU's 30-minute `min_interval` would otherwise push DOU to the next start, 45
minutes after its last request. The wait only decides when `collect_if_due`
is called; that still checks the interval against the clock after the wait.
"""

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from vacancy_radar.collect.service import CollectError, collect_if_due, describe, due_at
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging
from vacancy_radar.sources import ADAPTERS, SourceAdapter
from vacancy_radar.sources.http import build_client

logger = structlog.get_logger("vacancy_radar.collect")

Clock = Callable[[], datetime]
Sleep = Callable[[float], None]

# The job's starts drift by seconds (measured on 2026-11-13), so a few times
# that is enough, and it stays far below the job's 10-minute timeout.
MAX_WAIT = timedelta(minutes=2)
# `started_at` comes from whichever runner sent the request, so the wait ends
# a little after the due time in case runner clocks differ slightly. It is
# small because every wait moves the source's next due time later by as much.
CLOCK_MARGIN = timedelta(seconds=1)


def utc_now() -> datetime:
    return datetime.now(UTC)


def time_to_wait(due: datetime, now: datetime) -> timedelta | None:
    """How long to wait for a source due at `due`, or None to go ahead at once.

    None also when the source is due later than `MAX_WAIT` from now:
    `collect_if_due` then skips it, and a later start of the job collects it.
    """
    if due <= now or due - now > MAX_WAIT:
        return None
    return due - now + CLOCK_MARGIN


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m vacancy_radar.collect",
        description="Fetch vacancy sources once and store new and changed items.",
    )
    parser.add_argument(
        "sources",
        nargs="*",
        choices=sorted(ADAPTERS),
        metavar="SOURCE",
        help=f"sources to collect (default: all). Known: {', '.join(sorted(ADAPTERS))}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and parse, but do not store items (the request is still recorded)",
    )
    args = parser.parse_args(argv)
    # Not `default=`: argparse would validate the default list against `choices`.
    args.sources = args.sources or sorted(ADAPTERS)
    return args


def wait_until_due(adapter: SourceAdapter, *, clock: Clock, sleep: Sleep) -> None:
    """Sleep until the source is due if that is at most `MAX_WAIT` away.

    The due time is read in a session of its own, closed before sleeping, so
    no connection or transaction is held during the wait.
    """
    with get_sessionmaker()() as session:
        due = due_at(session, adapter)
    if due is None or (wait := time_to_wait(due, clock())) is None:
        return
    logger.info(
        "collect.waiting",
        source=adapter.slug,
        seconds=round(wait.total_seconds(), 1),
        due_at=due.isoformat(),
    )
    sleep(wait.total_seconds())


def run_one(
    adapter: SourceAdapter,
    client: httpx.Client,
    *,
    disabled: frozenset[str],
    dry_run: bool,
    clock: Clock,
    sleep: Sleep,
) -> None:
    if adapter.slug not in disabled:
        wait_until_due(adapter, clock=clock, sleep=sleep)

    with get_sessionmaker()() as session:
        outcome = collect_if_due(
            adapter,
            client=client,
            session=session,
            now=clock(),
            disabled=disabled,
            dry_run=dry_run,
        )

    event, fields = describe(outcome)
    logger.info(event, **fields)


def main(
    argv: Sequence[str] | None = None, *, clock: Clock = utc_now, sleep: Sleep = time.sleep
) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    failed = False
    with build_client() as client:
        for slug in args.sources:
            try:
                run_one(
                    ADAPTERS[slug],
                    client,
                    disabled=settings.disabled_sources,
                    dry_run=args.dry_run,
                    clock=clock,
                    sleep=sleep,
                )
            except CollectError as exc:
                failed = True
                logger.error(
                    "collect.failed",
                    source=slug,
                    error_type=type(exc.error).__name__,
                    error=str(exc.error),
                )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
