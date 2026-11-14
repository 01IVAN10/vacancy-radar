"""Command line entry point: `python -m vacancy_radar.analytics`.

Refreshes the analytics views (see `analytics.refresh`) and logs how many
jobs they count. Useful after a rebuild, so the pages need not wait for Beat.
"""

import argparse
import sys
from collections.abc import Sequence

import structlog

from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging

logger = structlog.get_logger("vacancy_radar.analytics")


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="python -m vacancy_radar.analytics",
        description="Refresh the analytics views from the current vacancies.",
    ).parse_args(argv)
    configure_logging(get_settings().log_level)
    with get_sessionmaker()() as session:
        jobs = refresh_views(session)
    logger.info("analytics.refreshed", jobs=jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
