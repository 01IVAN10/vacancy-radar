"""Command line entry point: `python -m vacancy_radar.clusters`.

Gives every vacancy without a cluster one, as `notify.match` does before it
matches (see `clusters.service`), and logs how many joined a cluster and how
many opened their own. Useful after a rebuild from raw data, or to see what
deduplication does without sending anything.
"""

import argparse
import sys
from collections.abc import Sequence

import structlog

from vacancy_radar.clusters.service import assign_clusters
from vacancy_radar.config import get_settings
from vacancy_radar.db.engine import get_sessionmaker
from vacancy_radar.log import configure_logging

logger = structlog.get_logger("vacancy_radar.clusters")


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="python -m vacancy_radar.clusters",
        description="Give every vacancy without a cluster the cluster of the same job, or its own.",
    ).parse_args(argv)
    configure_logging(get_settings().log_level)

    with get_sessionmaker()() as session:
        result = assign_clusters(session)
        session.commit()

    if result.busy:
        logger.info("clusters.busy")
    else:
        logger.info("clusters.assigned", opened=result.opened, joined=result.joined)
    return 0


if __name__ == "__main__":
    sys.exit(main())
