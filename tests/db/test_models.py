from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vacancy_radar.db.models import (
    CollectRun,
    RawVacancy,
    Source,
    Subscription,
    User,
    Vacancy,
)


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


def test_user_email_is_unique(db_session: Session) -> None:
    db_session.add(User(email="ivan@example.com", password_hash="x"))
    db_session.flush()

    db_session.add(User(email="ivan@example.com", password_hash="y"))
    with pytest.raises(IntegrityError, match="uq_app_user_email"):
        db_session.flush()


def test_user_email_must_be_lower_case(db_session: Session) -> None:
    # Keeps the unique constraint case-insensitive even for rows written by hand.
    db_session.add(User(email="Ivan@example.com", password_hash="x"))

    with pytest.raises(IntegrityError, match="ck_app_user_email_lowercase"):
        db_session.flush()


def subscription(user: User, **changes: Any) -> Subscription:
    values: dict[str, Any] = {
        "name": "Python remote",
        "skills": ["python"],
        "skills_match": "any",
        "seniority": [],
        "work_formats": [],
    }
    return Subscription(user_id=user.id, **(values | changes))


def test_subscription_defaults_to_active_without_a_salary_floor(db_session: Session) -> None:
    user = User(email="ivan@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    row = subscription(user)
    db_session.add(row)
    db_session.flush()
    db_session.refresh(row)

    assert (row.is_active, row.include_unsalaried, row.min_salary_usd) == (True, None, None)
    assert row.updated_at == row.created_at


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"name": ""}, "ck_subscription_name_length"),
        ({"name": "x" * 81}, "ck_subscription_name_length"),
        ({"skills_match": "one"}, "ck_subscription_skills_match"),
        # Canonical form: "all" means something only with two or more skills.
        ({"skills_match": "all"}, "ck_subscription_skills_match_needs_skills"),
        ({"skills_match": "all", "skills": []}, "ck_subscription_skills_match_needs_skills"),
        # Canonical form: the unsalaried choice exists exactly with a minimum.
        ({"include_unsalaried": False}, "ck_subscription_salary_complete"),
        ({"min_salary_usd": 3000}, "ck_subscription_salary_complete"),
        ({"seniority": ["guru"]}, "ck_subscription_seniority_known"),
        ({"work_formats": ["beach"]}, "ck_subscription_work_formats_known"),
        (
            {"min_salary_usd": 0, "include_unsalaried": True},
            "ck_subscription_min_salary_usd_positive",
        ),
    ],
)
def test_subscription_constraints(
    db_session: Session, changes: dict[str, Any], constraint: str
) -> None:
    user = User(email="ivan@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    db_session.add(subscription(user, **changes))

    with pytest.raises(IntegrityError, match=constraint):
        db_session.flush()


def test_unknown_skill_slugs_are_allowed_in_the_database(db_session: Session) -> None:
    # The dictionary changes; a removed skill must not invalidate stored rows.
    user = User(email="ivan@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    db_session.add(subscription(user, skills=["removed-skill"]))

    db_session.flush()


def test_all_skills_match_with_two_skills_and_a_full_salary_floor_is_valid(
    db_session: Session,
) -> None:
    user = User(email="ivan@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        subscription(
            user,
            skills=["python", "django"],
            skills_match="all",
            min_salary_usd=3000,
            include_unsalaried=False,
        )
    )

    db_session.flush()


def test_vacancy_dollar_range_cannot_start_above_its_end(db_session: Session) -> None:
    source = add_source(db_session, "djinni")
    raw = raw_vacancy(source)
    db_session.add(raw)
    db_session.flush()
    db_session.add(
        Vacancy(
            raw_vacancy_id=raw.id,
            source_id=source.id,
            url=raw.url,
            title="Python Developer",
            cities=[],
            salary_min=3000,
            salary_max=4000,
            salary_currency="USD",
            salary_period="month",
            salary_usd_min=4000,
            salary_usd_max=3000,
            seniority=[],
            work_formats=[],
            skills=[],
            description="",
            normalizer_version=2,
            raw_updated_at=datetime.now(UTC),
        )
    )

    with pytest.raises(IntegrityError, match="ck_vacancy_salary_usd_range"):
        db_session.flush()
