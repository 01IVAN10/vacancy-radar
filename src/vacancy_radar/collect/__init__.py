"""Collection: fetch a source and store its items in `raw_vacancy`.

Run it with `python -m vacancy_radar.collect`. The functions here are plain
code with no scheduler attached; the Celery task in stage 4 wraps them.
"""

from vacancy_radar.collect.service import (
    CollectStats,
    UpsertCounts,
    collect_source,
    ensure_source,
    upsert_raw_items,
)

__all__ = ["CollectStats", "UpsertCounts", "collect_source", "ensure_source", "upsert_raw_items"]
