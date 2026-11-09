"""Command line entry point: `python -m vacancy_radar.clusters`.

Gives every vacancy without a cluster one, as `notify.match` does before it
matches (see `clusters.service`), and logs how many joined a cluster and how
many opened their own. A backlog, such as a rebuild from raw data, is worked
off in runs of `MAX_PER_RUN`, each committed on its own. Useful to see what
deduplication does without sending anything.
"""

import argparse
import sys
from collections.abc import Sequence

import structlog

from vacancy_radar.clusters.service import MAX_PER_RUN, Busy, assign_clusters
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

    opened = joined = 0
    with get_sessionmaker()() as session:
        while True:
            run = assign_clusters(session, limit=MAX_PER_RUN)
            if isinstance(run, Busy):
                return 0  # `clusters.busy` is logged by the service
            opened += run.opened
            joined += run.joined
            if run.assigned < MAX_PER_RUN:
                break
    logger.info("clusters.assigned", opened=opened, joined=joined)
    return 0


if __name__ == "__main__":
    sys.exit(main())
