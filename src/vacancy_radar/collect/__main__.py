"""Command line entry point: `python -m vacancy_radar.collect [SOURCE ...]`.

Each source is collected in its own transaction. A failing source is logged
and the rest still run; the exit code is 1 if any source failed, so a cron
job shows up red.

The schedule runs more often than any source may be fetched. A source is
skipped when it is listed in `DISABLED_SOURCES`, when another collector is
fetching it right now, or when its `min_interval` has not passed since its
last request (`collect_run`). The checks live in `collect_if_due`, shared
with the Celery task.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
import structlog

from vacancy_radar.collect.service import collect_if_due, describe
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging
from vacancy_radar.sources import ADAPTERS, FeedError, SourceAdapter
from vacancy_radar.sources.http import build_client

logger = structlog.get_logger("vacancy_radar.collect")


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


def run_one(
    adapter: SourceAdapter, client: httpx.Client, *, disabled: frozenset[str], dry_run: bool
) -> None:
    with get_sessionmaker()() as session:
        outcome = collect_if_due(
            adapter,
            client=client,
            session=session,
            now=datetime.now(UTC),
            disabled=disabled,
            dry_run=dry_run,
        )

    event, fields = describe(outcome)
    logger.info(event, **fields)


def main(argv: Sequence[str] | None = None) -> int:
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
                )
            except (httpx.HTTPError, FeedError) as exc:
                failed = True
                logger.error(
                    "collect.failed", source=slug, error_type=type(exc).__name__, error=str(exc)
                )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
