from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.collect.service import (
    COLLECT_LOCK_NAMESPACE,
    MAX_ERROR_LENGTH,
    CollectStats,
    DryRunStats,
    Skipped,
    UpsertCounts,
    collect_if_due,
    collect_source,
    dry_run_source,
    ensure_source,
    is_due,
    last_attempt_at,
    try_lock_source,
    upsert_raw_items,
)
from vacancy_radar.db.models import CollectRun, RawVacancy, Source
from vacancy_radar.sources.base import FeedError, FetchResult, RawItem
from vacancy_radar.sources.djinni import parse_feed

ITEMS = parse_feed(fixture_bytes("djinni/rss_all.xml")).items


class FakeAdapter:
    slug = "fake"
    name = "Fake source"
    base_url = "https://fake.example"
    min_interval = timedelta(minutes=30)

    def __init__(self, result: FetchResult | Exception) -> None:
        self.result = result
        self.fetch_count = 0

    def fetch(self, client: httpx.Client) -> FetchResult:
        self.fetch_count += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def source_id(db_session: Session) -> int:
    return ensure_source(db_session, FakeAdapter(FetchResult(items=[])))


def runs(session: Session) -> list[CollectRun]:
    session.expire_all()
    return list(session.scalars(select(CollectRun).order_by(CollectRun.id)))


def stored(session: Session) -> dict[str, RawVacancy]:
    session.expire_all()
    return {row.external_id: row for row in session.scalars(select(RawVacancy))}


def test_first_run_inserts_every_item(db_session: Session, source_id: int) -> None:
    counts = upsert_raw_items(db_session, source_id, ITEMS)

    assert counts == UpsertCounts(inserted=5)
    rows = stored(db_session)
    assert set(rows) == {item.external_id for item in ITEMS}
    assert rows[ITEMS[0].external_id].payload == ITEMS[0].payload


def test_second_run_with_the_same_items_changes_nothing(
    db_session: Session, source_id: int
) -> None:
    upsert_raw_items(db_session, source_id, ITEMS)
    before = {key: (row.payload, row.updated_at) for key, row in stored(db_session).items()}

    counts = upsert_raw_items(db_session, source_id, ITEMS)

    assert counts == UpsertCounts(unchanged=5)
    after = {key: (row.payload, row.updated_at) for key, row in stored(db_session).items()}
    assert after == before


def test_changed_payload_overwrites_the_row(db_session: Session, source_id: int) -> None:
    upsert_raw_items(db_session, source_id, ITEMS)
    first_seen = stored(db_session)[ITEMS[0].external_id].first_seen_at
    edited = replace(ITEMS[0], payload={**ITEMS[0].payload, "title": "Edited title"})

    counts = upsert_raw_items(db_session, source_id, [edited, *ITEMS[1:]])

    assert counts == UpsertCounts(updated=1, unchanged=4)
    row = stored(db_session)[edited.external_id]
    assert row.payload["title"] == "Edited title"
    assert row.first_seen_at == first_seen


def test_repeated_item_in_one_batch_keeps_the_last_one(db_session: Session, source_id: int) -> None:
    first = RawItem(external_id="1", url="https://fake.example/1", payload={"v": 1})
    last = replace(first, payload={"v": 2})

    counts = upsert_raw_items(db_session, source_id, [first, last])

    assert counts == UpsertCounts(inserted=1)
    assert stored(db_session)["1"].payload == {"v": 2}


def test_empty_batch_writes_nothing(db_session: Session, source_id: int) -> None:
    assert upsert_raw_items(db_session, source_id, []) == UpsertCounts()
    assert stored(db_session) == {}


def test_ensure_source_is_idempotent(db_session: Session) -> None:
    adapter = FakeAdapter(FetchResult(items=[]))

    first = ensure_source(db_session, adapter)
    second = ensure_source(db_session, adapter)

    assert first == second
    assert db_session.scalar(select(func.count()).select_from(Source)) == 1


def test_collect_source_stores_items_and_reports_stats(db_session: Session) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS, skipped=1))

    with httpx.Client() as client:
        first = collect_source(adapter, client=client, session=db_session)
        second = collect_source(adapter, client=client, session=db_session)

    assert first == CollectStats(
        source="fake", fetched=5, skipped=1, inserted=5, updated=0, unchanged=0
    )
    assert second == CollectStats(
        source="fake", fetched=5, skipped=1, inserted=0, updated=0, unchanged=5
    )
    assert len(stored(db_session)) == 5


def test_collect_source_records_each_successful_run(db_session: Session) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS, skipped=1))

    with httpx.Client() as client:
        collect_source(adapter, client=client, session=db_session)
        collect_source(adapter, client=client, session=db_session)

    first, second = runs(db_session)
    assert first.status == second.status == "ok"
    assert (first.fetched, first.skipped, first.inserted, first.unchanged) == (5, 1, 5, 0)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 5)
    assert first.error_type is None
    assert first.started_at <= first.finished_at


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("connection refused"), FeedError("not an RSS feed")],
)
def test_failed_fetch_is_recorded_and_raised_again(db_session: Session, error: Exception) -> None:
    adapter = FakeAdapter(error)

    with httpx.Client() as client, pytest.raises(type(error)):
        collect_source(adapter, client=client, session=db_session)

    [run] = runs(db_session)
    assert run.status == "failed"
    assert run.error_type == type(error).__name__
    assert run.error == str(error)
    assert run.fetched is None
    assert stored(db_session) == {}


def test_long_error_message_is_truncated(db_session: Session) -> None:
    adapter = FakeAdapter(FeedError("x" * 5000))

    with httpx.Client() as client, pytest.raises(FeedError):
        collect_source(adapter, client=client, session=db_session)

    [run] = runs(db_session)
    assert run.error == "x" * MAX_ERROR_LENGTH


def test_dry_run_records_the_request_without_storing_items(db_session: Session) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS, skipped=1))

    with httpx.Client() as client:
        result = dry_run_source(adapter, client=client, session=db_session)

    assert len(result.items) == 5
    [run] = runs(db_session)
    assert (run.status, run.fetched, run.skipped, run.inserted) == ("dry_run", 5, 1, None)
    assert stored(db_session) == {}


def test_failed_dry_run_is_recorded_as_failed(db_session: Session) -> None:
    adapter = FakeAdapter(FeedError("not an RSS feed"))

    with httpx.Client() as client, pytest.raises(FeedError):
        dry_run_source(adapter, client=client, session=db_session)

    assert [run.status for run in runs(db_session)] == ["failed"]


def test_source_without_runs_is_due(db_session: Session) -> None:
    adapter = FakeAdapter(FetchResult(items=[]))

    assert last_attempt_at(db_session, adapter) is None
    assert is_due(db_session, adapter, now=datetime.now(UTC))


@pytest.mark.parametrize(
    ("elapsed", "due"),
    [
        (timedelta(minutes=29, seconds=59), False),
        (timedelta(minutes=30), True),
        (timedelta(hours=2), True),
    ],
)
def test_source_is_due_once_its_min_interval_has_passed(
    db_session: Session, elapsed: timedelta, due: bool
) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS))
    with httpx.Client() as client:
        collect_source(adapter, client=client, session=db_session)
    [run] = runs(db_session)

    assert last_attempt_at(db_session, adapter) == run.started_at
    assert is_due(db_session, adapter, now=run.started_at + elapsed) is due


def test_due_check_looks_only_at_the_same_source(db_session: Session) -> None:
    other = FakeAdapter(FetchResult(items=ITEMS))
    other.slug = "other"
    with httpx.Client() as client:
        collect_source(other, client=client, session=db_session)

    assert is_due(db_session, FakeAdapter(FetchResult(items=[])), now=datetime.now(UTC))


@pytest.fixture
def client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


@pytest.fixture
def other_collector(migrated_engine: Engine) -> Iterator[Connection]:
    """A second database connection, standing in for a collector running in parallel."""
    with migrated_engine.connect() as connection:
        try:
            yield connection
        finally:
            connection.rollback()


def hold_lock(connection: Connection, slug: str) -> None:
    connection.execute(
        select(func.pg_advisory_xact_lock(COLLECT_LOCK_NAMESPACE, func.hashtext(slug)))
    )


def test_collect_if_due_collects_a_due_source(db_session: Session, client: httpx.Client) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS))

    outcome = collect_if_due(adapter, client=client, session=db_session, now=datetime.now(UTC))

    assert outcome == CollectStats(
        source="fake", fetched=5, skipped=0, inserted=5, updated=0, unchanged=0
    )
    assert [run.status for run in runs(db_session)] == ["ok"]


def test_collect_if_due_dry_run_stores_nothing(db_session: Session, client: httpx.Client) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS, skipped=1))

    outcome = collect_if_due(
        adapter, client=client, session=db_session, now=datetime.now(UTC), dry_run=True
    )

    assert outcome == DryRunStats(source="fake", fetched=5, skipped=1)
    assert stored(db_session) == {}
    assert [run.status for run in runs(db_session)] == ["dry_run"]


def test_collect_if_due_skips_a_disabled_source(db_session: Session, client: httpx.Client) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS))

    outcome = collect_if_due(
        adapter, client=client, session=db_session, now=datetime.now(UTC), disabled={"fake"}
    )

    assert outcome == Skipped(source="fake", reason="disabled")
    assert adapter.fetch_count == 0
    assert runs(db_session) == []


def test_collect_if_due_skips_a_source_requested_too_recently(
    db_session: Session, client: httpx.Client
) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS))
    collect_source(adapter, client=client, session=db_session)
    [run] = runs(db_session)

    outcome = collect_if_due(
        adapter, client=client, session=db_session, now=run.started_at + timedelta(minutes=1)
    )

    assert outcome == Skipped(source="fake", reason="min_interval", last_attempt_at=run.started_at)
    assert adapter.fetch_count == 1


def test_collect_if_due_skips_a_source_another_collector_is_fetching(
    db_session: Session, client: httpx.Client, other_collector: Connection
) -> None:
    adapter = FakeAdapter(FetchResult(items=ITEMS))
    hold_lock(other_collector, adapter.slug)

    outcome = collect_if_due(adapter, client=client, session=db_session, now=datetime.now(UTC))

    assert outcome == Skipped(source="fake", reason="busy")
    assert adapter.fetch_count == 0


def test_lock_of_one_source_does_not_block_another(
    db_session: Session, other_collector: Connection
) -> None:
    hold_lock(other_collector, "other")

    assert try_lock_source(db_session, FakeAdapter(FetchResult(items=[])))


def test_lock_is_released_when_the_transaction_ends(
    migrated_engine: Engine, other_collector: Connection
) -> None:
    adapter = FakeAdapter(FetchResult(items=[]))
    with Session(migrated_engine) as first:
        assert try_lock_source(first, adapter)
        assert not other_collector.scalar(
            select(func.pg_try_advisory_xact_lock(COLLECT_LOCK_NAMESPACE, func.hashtext("fake")))
        )
        first.rollback()

    assert other_collector.scalar(
        select(func.pg_try_advisory_xact_lock(COLLECT_LOCK_NAMESPACE, func.hashtext("fake")))
    )
