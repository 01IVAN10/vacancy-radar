from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vacancy_radar.db.models import CollectRun, RawVacancy, Source


def add_source(session: Session, slug: str) -> Source:
    source = Source(slug=slug, name=slug.title(), base_url=f"https://{slug}.example")
    session.add(source)
    session.flush()
    return source


def raw_vacancy(source: Source, external_id: str = "1") -> RawVacancy:
    return RawVacancy(
        source_id=source.id,
        external_id=external_id,
        url=f"{source.base_url}/jobs/{external_id}",
        payload={"title": "Python Developer"},
    )


def test_server_fills_timestamps(db_session: Session) -> None:
    vacancy = raw_vacancy(add_source(db_session, "djinni"))
    db_session.add(vacancy)
    db_session.flush()
    db_session.refresh(vacancy)

    assert isinstance(vacancy.first_seen_at, datetime)
    assert vacancy.first_seen_at.tzinfo is not None
    assert vacancy.updated_at == vacancy.first_seen_at


def test_external_id_is_unique_within_a_source(db_session: Session) -> None:
    source = add_source(db_session, "djinni")
    db_session.add(raw_vacancy(source, "42"))
    db_session.flush()

    db_session.add(raw_vacancy(source, "42"))
    with pytest.raises(IntegrityError, match="uq_raw_vacancy_source_id_external_id"):
        db_session.flush()


def test_same_external_id_is_allowed_in_different_sources(db_session: Session) -> None:
    db_session.add_all(
        [
            raw_vacancy(add_source(db_session, "djinni"), "42"),
            raw_vacancy(add_source(db_session, "dou"), "42"),
        ]
    )
    db_session.flush()

    count = len(db_session.scalars(select(RawVacancy).where(RawVacancy.external_id == "42")).all())
    assert count == 2


def test_source_slug_is_unique(db_session: Session) -> None:
    add_source(db_session, "djinni")

    with pytest.raises(IntegrityError, match="uq_source_slug"):
        add_source(db_session, "djinni")


def test_payload_round_trips_unchanged(db_session: Session) -> None:
    payload = {
        "title": "Python розробник / Developer",
        # Non-breaking and thin spaces plus an en dash, as Djinni sends them.
        "description": "<p>Salary 40\u00a0000\u2009\u2013\u200960\u00a0000 UAH</p>",
        "categories": ["Python", ""],
        "nested": {"n": 1, "ok": True, "missing": None},
    }
    vacancy = raw_vacancy(add_source(db_session, "djinni"))
    vacancy.payload = payload
    db_session.add(vacancy)
    db_session.flush()
    db_session.expire_all()

    stored = db_session.scalars(select(RawVacancy.payload)).one()
    assert stored == payload


@pytest.mark.parametrize(
    ("status", "finished"),
    [("running", True), ("ok", False), ("failed", False)],
)
def test_only_a_running_collect_run_has_no_finished_at(
    db_session: Session, status: str, finished: bool
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        CollectRun(
            source_id=add_source(db_session, "djinni").id,
            started_at=now,
            finished_at=now if finished else None,
            status=status,
        )
    )

    with pytest.raises(IntegrityError, match="ck_collect_run_finished"):
        db_session.flush()
