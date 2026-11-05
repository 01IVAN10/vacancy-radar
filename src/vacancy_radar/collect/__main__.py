"""Command line entry point: `python -m vacancy_radar.collect [SOURCE ...]`.

Each source is collected in its own transaction. A failing source is logged
and the rest still run; the exit code is 1 if any source failed, so a cron
job shows up red.

The schedule runs more often than any source may be fetched. A source is
skipped when it is listed in `DISABLED_SOURCES`, or when its `min_interval`
has not passed since its last request (`collect_run`).
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime

import httpx
import structlog

from vacancy_radar.collect.service import (
    collect_source,
    dry_run_source,
    is_due,
    last_attempt_at,
)
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


def run_one(adapter: SourceAdapter, client: httpx.Client, *, dry_run: bool) -> None:
    with get_sessionmaker()() as session:
        if not is_due(session, adapter, now=datetime.now(UTC)):
            last = last_attempt_at(session, adapter)
            logger.info(
                "collect.skipped",
                source=adapter.slug,
                reason="min_interval",
                last_attempt_at=last.isoformat() if last else None,
            )
            return

        if dry_run:
            result = dry_run_source(adapter, client=client, session=session)
            logger.info(
                "collect.dry_run",
                source=adapter.slug,
                fetched=len(result.items),
                skipped=result.skipped,
            )
            return

        stats = collect_source(adapter, client=client, session=session)
    logger.info("collect.finished", **asdict(stats))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    failed = False
    with build_client() as client:
        for slug in args.sources:
            if slug in settings.disabled_sources:
                logger.info("collect.skipped", source=slug, reason="disabled")
                continue
            try:
                run_one(ADAPTERS[slug], client, dry_run=args.dry_run)
            except (httpx.HTTPError, FeedError) as exc:
                failed = True
                logger.error(
                    "collect.failed", source=slug, error_type=type(exc).__name__, error=str(exc)
                )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
