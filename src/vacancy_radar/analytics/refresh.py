"""Rebuild the analytics views from the current vacancies and clusters.

`REFRESH MATERIALIZED VIEW CONCURRENTLY` builds each view anew next to the
old one and swaps the changed rows in, so pages keep reading the old rows
meanwhile. Two refreshes of one view wait for each other, so no lock of our
own is needed, and running it twice only repeats the work.
"""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from vacancy_radar.analytics.views import MATERIALIZED_VIEWS, summary


def refresh_views(session: Session) -> int:
    """Refresh every analytics view and commit. Returns how many jobs they now count."""
    for view in MATERIALIZED_VIEWS:
        session.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view.name}"))
    jobs = session.scalar(select(summary.c.jobs)) or 0
    session.commit()
    return jobs
