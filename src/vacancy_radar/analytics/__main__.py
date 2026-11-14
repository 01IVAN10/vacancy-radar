"""Command line entry point: `python -m vacancy_radar.analytics`.

Refreshes the analytics views (see `analytics.refresh`), which logs how
many jobs they count. Useful after a rebuild, so the pages need not wait for Beat.
"""

import argparse
import sys
from collections.abc import Sequence

from vacancy_radar.analytics.refresh import refresh_views
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="python -m vacancy_radar.analytics",
        description="Refresh the analytics views from the current vacancies.",
    ).parse_args(argv)
    configure_logging(get_settings().log_level)
    with get_sessionmaker()() as session:
        refresh_views(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
