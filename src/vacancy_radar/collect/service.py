"""Store fetched items idempotently.

Idempotency is enforced by the database: `raw_vacancy` is unique on
`(source_id, external_id)`, and every write is an `INSERT … ON CONFLICT`.
Collecting the same feed twice leaves the table as it was after the first run.

Every attempt, successful or not, is also recorded in `collect_run`. The run is
committed as `running` before the request is sent and finished afterwards, so a
request counts against the source's `min_interval` whatever fails after it.

`collect_if_due` is the entry point for every scheduler (the CLI and the Celery
task): it applies the kill switch, the per-source lock and `min_interval`
before any request is sent.
"""

from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx
from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vacancy_radar.db import locks
from vacancy_radar.db.models import CollectRun, RawVacancy, Source
from vacancy_radar.sources.base import RawItem, SourceAdapter

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


@dataclass(frozen=True, slots=True)
class DryRunStats:
    """Outcome of a dry run: the source was fetched, nothing was stored."""

    source: str
    fetched: int
    skipped: int


SkipReason = Literal["disabled", "busy", "min_interval", "superseded"]


@dataclass(frozen=True, slots=True)
class Skipped:
    """The source was not requested.

    `disabled`: listed in `DISABLED_SOURCES`. `busy`: another collector holds
    the source's lock right now. `min_interval`: requested too recently.
    `superseded`: a retry whose failed attempt is no longer the source's latest,
    because another attempt was made since (or the retry was delivered twice).
    """

    source: str
    reason: SkipReason
    last_attempt_at: datetime | None = None


CollectOutcome = CollectStats | DryRunStats | Skipped


class CollectError(Exception):
    """An attempt failed after its run was recorded as `failed`.

    `error` is what went wrong: a network or HTTP error, an unparsable feed, a
    database error while storing the items, or anything else. `run_id` is the
    failed run, which a retry passes back to `collect_if_due` as `retry_of`.
    """

    def __init__(self, run_id: int, error: Exception) -> None:
        super().__init__(f"{type(error).__name__}: {error}")
        self.run_id = run_id
        self.error = error


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


def last_attempt_at(session: Session, adapter: SourceAdapter) -> datetime | None:
    """When the source was last requested, by any kind of run."""
    return session.scalar(
        select(func.max(CollectRun.started_at))
        .join(Source, Source.id == CollectRun.source_id)
        .where(Source.slug == adapter.slug)
    )


def due_at(session: Session, adapter: SourceAdapter) -> datetime | None:
    """When the source may be requested again, or None if it never was.

    That is `adapter.min_interval` after the last request. Failed and dry runs
    count too: they sent a request to the source as well.
    """
    last = last_attempt_at(session, adapter)
    return None if last is None else last + adapter.min_interval


def is_due(session: Session, adapter: SourceAdapter, *, now: datetime) -> bool:
    """Whether at least `adapter.min_interval` has passed since the last request."""
    due = due_at(session, adapter)
    return due is None or now >= due


def latest_attempt_id(session: Session, adapter: SourceAdapter) -> int | None:
    """The id of the source's latest run.

    Runs are started under the source's lock, so a later run has a larger id.
    """
    return session.scalar(
        select(func.max(CollectRun.id))
        .join(Source, Source.id == CollectRun.source_id)
        .where(Source.slug == adapter.slug)
    )


def try_lock_source(session: Session, adapter: SourceAdapter) -> bool:
    """Take the source's collection lock for the rest of the transaction.

    Returns False at once if another transaction holds it. Without the lock,
    two collectors started together (a scheduled task and a retry, say) would
    both find the source due and both start a run. The lock is held only from
    the due check until the new `running` run is committed: from then on the
    run itself makes the source not due. Postgres releases the lock on commit
    or rollback, so a crashed collector never leaves it behind.
    """
    locked = session.scalar(
        select(func.pg_try_advisory_xact_lock(locks.COLLECT, func.hashtext(adapter.slug)))
    )
    return bool(locked)


def check_due(
    session: Session, adapter: SourceAdapter, *, now: datetime, retry_of: int | None
) -> Skipped | None:
    """None if the source may be requested now, or why it may not.

    A scheduled run waits for `min_interval`. A retry (`retry_of`, the id of
    the failed run it repeats) does not, because that failed run would make it
    wait the full interval. It may run only while its failed run is still the
    latest one, so a retry delivered twice, or one that another collector got
    ahead of, sends no request.
    """
    if retry_of is None:
        if is_due(session, adapter, now=now):
            return None
        reason: SkipReason = "min_interval"
    else:
        if latest_attempt_id(session, adapter) == retry_of:
            return None
        reason = "superseded"
    return Skipped(
        source=adapter.slug, reason=reason, last_attempt_at=last_attempt_at(session, adapter)
    )


def collect_if_due(
    adapter: SourceAdapter,
    *,
    client: httpx.Client,
    session: Session,
    now: datetime,
    disabled: Collection[str] = frozenset(),
    dry_run: bool = False,
    retry_of: int | None = None,
) -> CollectOutcome:
    """Collect one source unless it is disabled, locked, or not due yet.

    The checks run in that order. The lock is taken before the due check and
    held until the new run is committed, so no other collector can start a
    run for the same source in between. A failed attempt raises
    `CollectError`, as in `collect_source`.

    `retry_of` marks a retry of that failed run: see `check_due`. How many
    retries a source gets is `adapter.max_retries`.
    """
    if adapter.slug in disabled:
        return Skipped(source=adapter.slug, reason="disabled")
    if not try_lock_source(session, adapter):
        return Skipped(source=adapter.slug, reason="busy")
    if (skipped := check_due(session, adapter, now=now, retry_of=retry_of)) is not None:
        return skipped

    if dry_run:
        return dry_run_source(adapter, client=client, session=session)
    return collect_source(adapter, client=client, session=session)


def describe(outcome: CollectOutcome) -> tuple[str, dict[str, object]]:
    """The log event and JSON-safe fields for an outcome, shared by the CLI and the task."""
    match outcome:
        case Skipped(last_attempt_at=last):
            return "collect.skipped", {
                "source": outcome.source,
                "reason": outcome.reason,
                "last_attempt_at": last.isoformat() if last else None,
            }
        case DryRunStats():
            return "collect.dry_run", asdict(outcome)
        case CollectStats():
            return "collect.finished", asdict(outcome)


def collect_source(
    adapter: SourceAdapter, *, client: httpx.Client, session: Session
) -> CollectStats:
    """Request one source, store its items and finish its run, without any checks.

    The run is committed as `running` first. The items and the finished run
    are then written in one transaction. On any error the run is finished as
    `failed` and `CollectError` is raised.
    """
    source_id = ensure_source(session, adapter)
    run_id = start_run(session, source_id)
    with failing_run(session, run_id):
        result = adapter.fetch(client)
        counts = upsert_raw_items(session, source_id, result.items)
        stats = CollectStats(
            source=adapter.slug,
            fetched=len(result.items),
            skipped=result.skipped,
            inserted=counts.inserted,
            updated=counts.updated,
            unchanged=counts.unchanged,
        )
        finish_run(
            session,
            run_id,
            status="ok",
            fetched=stats.fetched,
            skipped=stats.skipped,
            inserted=stats.inserted,
            updated=stats.updated,
            unchanged=stats.unchanged,
        )
        session.commit()
    return stats


def dry_run_source(
    adapter: SourceAdapter, *, client: httpx.Client, session: Session
) -> DryRunStats:
    """Request and parse one source without storing its items.

    The request is still recorded as a `dry_run` run, because it counts
    against the source's `min_interval` like any other request.
    """
    run_id = start_run(session, ensure_source(session, adapter))
    with failing_run(session, run_id):
        result = adapter.fetch(client)
        finish_run(
            session, run_id, status="dry_run", fetched=len(result.items), skipped=result.skipped
        )
        session.commit()
    return DryRunStats(source=adapter.slug, fetched=len(result.items), skipped=result.skipped)


def start_run(session: Session, source_id: int) -> int:
    """Commit a `running` run before the request is sent, and return its id.

    The commit ends the transaction, so none is held open during the request.
    """
    run = CollectRun(source_id=source_id, started_at=datetime.now(UTC), status="running")
    session.add(run)
    session.flush()
    run_id = run.id
    session.commit()
    return run_id


def finish_run(session: Session, run_id: int, *, status: str, **fields: int | str | None) -> None:
    """Set the run's final status and fields. The caller commits."""
    session.execute(
        update(CollectRun)
        .where(CollectRun.id == run_id)
        .values(status=status, finished_at=datetime.now(UTC), **fields)
    )


@contextmanager
def failing_run(session: Session, run_id: int) -> Iterator[None]:
    """On any error inside, finish the run as `failed` and raise `CollectError`.

    The failed transaction is rolled back first, so nothing it wrote is kept.
    If the database cannot record the failure either, that error propagates
    and the run stays `running`, which still counts as a request.
    """
    try:
        yield
    except Exception as exc:
        session.rollback()
        finish_run(
            session,
            run_id,
            status="failed",
            error_type=type(exc).__name__,
            error=str(exc)[:MAX_ERROR_LENGTH],
        )
        session.commit()
        raise CollectError(run_id, exc) from exc
