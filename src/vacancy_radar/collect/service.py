"""Store fetched items idempotently.

Idempotency is enforced by the database: `raw_vacancy` is unique on
`(source_id, external_id)`, and every write is an `INSERT … ON CONFLICT`.
Collecting the same feed twice leaves the table as it was after the first run.

Every attempt, successful or not, is also recorded in `collect_run`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vacancy_radar.db.models import CollectRun, RawVacancy, Source
from vacancy_radar.sources.base import FeedError, RawItem, SourceAdapter

# Enough to identify the failure; a long HTML error page is not worth storing.
MAX_ERROR_LENGTH = 1000


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True, slots=True)
class CollectStats:
    """Outcome of one collection run for one source.

    `unchanged + updated` is the overlap with earlier runs. If it drops to 0,
    the feed rolled past items between two runs and some were missed.
    """

    source: str
    fetched: int
    skipped: int
    inserted: int
    updated: int
    unchanged: int


def ensure_source(session: Session, adapter: SourceAdapter) -> int:
    """Return the id of the adapter's `source` row, creating it on first use.

    Sources are registered from code, so adding an adapter needs no migration.
    """
    values = insert(Source).values(slug=adapter.slug, name=adapter.name, base_url=adapter.base_url)
    upsert = values.on_conflict_do_update(
        index_elements=[Source.slug],
        set_={"name": values.excluded.name, "base_url": values.excluded.base_url},
    ).returning(Source.id)
    return session.scalars(upsert).one()


def upsert_raw_items(session: Session, source_id: int, items: Sequence[RawItem]) -> UpsertCounts:
    """Insert new items, update changed ones, leave identical ones untouched.

    An existing row is rewritten only when its payload differs, and only then
    does `updated_at` move. `first_seen_at` is never changed.
    """
    # Postgres rejects one INSERT … ON CONFLICT that hits the same row twice,
    # so a feed that repeats an item keeps only its last occurrence.
    unique = {item.external_id: item for item in items}
    if not unique:
        return UpsertCounts()

    values = insert(RawVacancy).values(
        [
            {
                "source_id": source_id,
                "external_id": item.external_id,
                "url": item.url,
                "payload": item.payload,
            }
            for item in unique.values()
        ]
    )
    upsert = values.on_conflict_do_update(
        index_elements=[RawVacancy.source_id, RawVacancy.external_id],
        set_={
            "url": values.excluded.url,
            "payload": values.excluded.payload,
            "updated_at": func.now(),
        },
        where=RawVacancy.payload.is_distinct_from(values.excluded.payload),
    ).returning(
        # `xmax` is 0 for a freshly inserted row version and set for an updated one.
        # Rows skipped by the WHERE above are not returned at all.
        literal_column("xmax") == 0
    )
    written = session.scalars(upsert).all()

    inserted = sum(written)
    updated = len(written) - inserted
    return UpsertCounts(inserted=inserted, updated=updated, unchanged=len(unique) - len(written))


def collect_source(
    adapter: SourceAdapter, *, client: httpx.Client, session: Session
) -> CollectStats:
    """Fetch one source, store its items and record the run in one transaction.

    A failed fetch is recorded as a `failed` run and committed, then the error
    is raised again so the caller still sees it.
    """
    started_at = datetime.now(UTC)
    try:
        result = adapter.fetch(client)
    except (httpx.HTTPError, FeedError) as exc:
        session.add(
            CollectRun(
                source_id=ensure_source(session, adapter),
                started_at=started_at,
                finished_at=datetime.now(UTC),
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc)[:MAX_ERROR_LENGTH],
            )
        )
        session.commit()
        raise

    source_id = ensure_source(session, adapter)
    counts = upsert_raw_items(session, source_id, result.items)
    stats = CollectStats(
        source=adapter.slug,
        fetched=len(result.items),
        skipped=result.skipped,
        inserted=counts.inserted,
        updated=counts.updated,
        unchanged=counts.unchanged,
    )
    session.add(
        CollectRun(
            source_id=source_id,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            status="ok",
            fetched=stats.fetched,
            skipped=stats.skipped,
            inserted=stats.inserted,
            updated=stats.updated,
            unchanged=stats.unchanged,
        )
    )
    session.commit()
    return stats
