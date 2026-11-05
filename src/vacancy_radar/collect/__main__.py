"""Command line entry point: `python -m vacancy_radar.collect [SOURCE ...]`.

Each source is collected in its own transaction. A failing source is logged
and the rest still run; the exit code is 1 if any source failed, so a cron
job shows up red.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict

import httpx
import structlog

from vacancy_radar.collect.service import collect_source
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
        help="fetch and parse, but do not touch the database",
    )
    args = parser.parse_args(argv)
    # Not `default=`: argparse would validate the default list against `choices`.
    args.sources = args.sources or sorted(ADAPTERS)
    return args


def run_one(adapter: SourceAdapter, client: httpx.Client, *, dry_run: bool) -> None:
    if dry_run:
        result = adapter.fetch(client)
        logger.info(
            "collect.dry_run",
            source=adapter.slug,
            fetched=len(result.items),
            skipped=result.skipped,
        )
        return

    with get_sessionmaker()() as session:
        stats = collect_source(adapter, client=client, session=session)
    logger.info("collect.finished", **asdict(stats))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(get_settings().log_level)

    failed = False
    with build_client() as client:
        for slug in args.sources:
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
