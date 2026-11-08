"""Rows for notification tests, written straight to the tables.

Vacancies are inserted already normalized instead of going through the
parsers, so each test states exactly the fields matching reads.
"""

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from sqlalchemy.orm import Session

from vacancy_radar.collect.service import ensure_source
from vacancy_radar.db.models import RawVacancy, Subscription, User, Vacancy
from vacancy_radar.domain.normalize import NORMALIZER_VERSION
from vacancy_radar.notify.clusters import assign_clusters
from vacancy_radar.sources import ADAPTERS

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
# Before every vacancy the tests add, unless a test says otherwise.
LINKED_AT = NOW - timedelta(days=1)
CHAT_ID = 123456789

_ids = count(1)


def add_user(
    session: Session,
    email: str = "ivan@example.com",
    *,
    chat_id: int | None = CHAT_ID,
    linked_at: datetime = LINKED_AT,
) -> User:
    user = User(
        email=email,
        password_hash="not-a-real-hash",
        telegram_chat_id=chat_id,
        telegram_linked_at=linked_at if chat_id is not None else None,
    )
    session.add(user)
    session.flush()
    return user


def add_subscription(
    session: Session,
    user: User,
    name: str = "Python",
    *,
    skills: list[str] | None = None,
    active_since: datetime = LINKED_AT,
    is_active: bool = True,
    **columns: Any,
) -> Subscription:
    subscription = Subscription(
        user_id=user.id,
        name=name,
        skills=["python"] if skills is None else skills,
        skills_match="any",
        seniority=[],
        work_formats=[],
        active_since=active_since,
        is_active=is_active,
        **columns,
    )
    session.add(subscription)
    session.flush()
    return subscription


def add_vacancy(
    session: Session,
    *,
    first_seen_at: datetime = NOW - timedelta(hours=2),
    normalized_at: datetime = NOW - timedelta(hours=2),
    title: str = "Python Developer",
    skills: list[str] | None = None,
    **columns: Any,
) -> Vacancy:
    """A normalized Djinni vacancy whose raw item was first seen at `first_seen_at`."""
    external_id = str(next(_ids))
    url = f"https://djinni.co/jobs/{external_id}/"
    raw = RawVacancy(
        source_id=ensure_source(session, ADAPTERS["djinni"]),
        external_id=external_id,
        url=url,
        payload={},
        first_seen_at=first_seen_at,
    )
    session.add(raw)
    session.flush()
    vacancy = Vacancy(
        raw_vacancy_id=raw.id,
        source_id=raw.source_id,
        url=url,
        title=title,
        company="Acme",
        cities=["Kyiv"],
        skills=["python"] if skills is None else skills,
        seniority=[],
        work_formats=[],
        normalizer_version=NORMALIZER_VERSION,
        raw_updated_at=first_seen_at,
        normalized_at=normalized_at,
        **columns,
    )
    session.add(vacancy)
    session.flush()
    return vacancy


def add_clustered_vacancy(session: Session, **fields: Any) -> Vacancy:
    vacancy = add_vacancy(session, **fields)
    assign_clusters(session)
    session.refresh(vacancy)
    return vacancy
