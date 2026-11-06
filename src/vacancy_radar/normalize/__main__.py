"""Command line entry point: `python -m vacancy_radar.normalize [--rebuild]`.

Normalizes every pending raw item (see `normalize.service`). The exit code is
1 if NBU could not be reached for a rate; the items of the failed batch stay
pending for the next run.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict

import httpx
import structlog

from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging
from vacancy_radar.normalize.service import normalize_pending
from vacancy_radar.sources.base import FeedError
from vacancy_radar.sources.http import build_client

logger = structlog.get_logger("vacancy_radar.normalize")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m vacancy_radar.normalize",
        description="Rebuild vacancy rows from raw_vacancy items that are new or changed.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="normalize every raw item, not only pending ones",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(get_settings().log_level)

    try:
        with build_client() as client, get_sessionmaker()() as session:
            stats = normalize_pending(session, client, rebuild=args.rebuild)
    except (httpx.HTTPError, FeedError) as exc:
        logger.error("normalize.failed", error_type=type(exc).__name__, error=str(exc))
        return 1

    logger.info("normalize.finished", rebuild=args.rebuild, **asdict(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
