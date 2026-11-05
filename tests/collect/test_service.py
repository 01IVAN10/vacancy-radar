from dataclasses import replace

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.collect.service import (
    CollectStats,
    UpsertCounts,
    collect_source,
    ensure_source,
    upsert_raw_items,
)
from vacancy_radar.db.models import RawVacancy, Source
from vacancy_radar.sources.base import FetchResult, RawItem
from vacancy_radar.sources.djinni import parse_feed

ITEMS = parse_feed(fixture_bytes("djinni/rss_all.xml")).items


class FakeAdapter:
    slug = "fake"
    name = "Fake source"
    base_url = "https://fake.example"

    def __init__(self, result: FetchResult) -> None:
        self.result = result

    def fetch(self, client: httpx.Client) -> FetchResult:
        return self.result


@pytest.fixture
def source_id(db_session: Session) -> int:
    return ensure_source(db_session, FakeAdapter(FetchResult(items=[])))


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
