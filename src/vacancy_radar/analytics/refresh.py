"""Rebuild the analytics views from the current vacancies and clusters.

`REFRESH MATERIALIZED VIEW CONCURRENTLY` builds each view anew next to the
old one and swaps the changed rows in, so pages keep reading the old rows
meanwhile. The views are refreshed in dependency order in one transaction,
so readers see every view of the old refresh or every view of the new one.
Two refreshes of one view wait for each other, so no lock of our own is
needed, and running it twice only repeats the work.
"""

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from vacancy_radar.analytics.views import MATERIALIZED_VIEWS, summary

logger = structlog.get_logger("vacancy_radar.analytics")


def refresh_views(session: Session) -> int:
    """Refresh every analytics view, commit, and log it. Returns how many jobs they now count."""
    for view in MATERIALIZED_VIEWS:
        session.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view.name}"))
    jobs = session.scalar(select(summary.c.jobs)) or 0
    session.commit()
    logger.info("analytics.refreshed", jobs=jobs)
    return jobs
