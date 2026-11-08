"""Queuing matches: who gets which cluster, and only once."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.notify.factories import (
    LINKED_AT,
    NOW,
    add_clustered_vacancy,
    add_subscription,
    add_user,
    add_vacancy,
)
from vacancy_radar.db.models import Notification
from vacancy_radar.notify.matching import CONVERSION_GRACE, LOOKBACK, match_new


def queued(session: Session) -> list[tuple[int, int, int, str]]:
    rows = session.execute(
        select(
            Notification.subscription_id,
            Notification.cluster_id,
            Notification.vacancy_id,
            Notification.status,
        ).order_by(Notification.id)
    )
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def test_queues_a_new_matching_cluster(db_session: Session) -> None:
    subscription = add_subscription(db_session, add_user(db_session))
    vacancy = add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == 1

    assert vacancy.cluster_id is not None
    assert queued(db_session) == [(subscription.id, vacancy.cluster_id, vacancy.id, "pending")]
    row = db_session.scalars(select(Notification)).one()
    assert row.next_attempt_at == NOW
    assert row.attempts == 0


def test_running_again_queues_nothing_new(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session))
    add_clustered_vacancy(db_session)
    match_new(db_session, now=NOW)

    assert match_new(db_session, now=NOW) == 0
    assert len(queued(db_session)) == 1


def test_a_vacancy_that_does_not_match_is_not_queued(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session), skills=["go"])
    add_clustered_vacancy(db_session, skills=["python"])

    assert match_new(db_session, now=NOW) == 0


def test_each_matching_subscription_gets_its_row(db_session: Session) -> None:
    user = add_user(db_session)
    python = add_subscription(db_session, user, "Python")
    backend = add_subscription(db_session, user, "Backend", skills=["python", "django"])
    vacancy = add_clustered_vacancy(db_session, skills=["python"])

    assert match_new(db_session, now=NOW) == 2
    assert {row[0] for row in queued(db_session)} == {python.id, backend.id}
    assert {row[1] for row in queued(db_session)} == {vacancy.cluster_id}


def test_nothing_first_seen_before_the_chat_was_linked(db_session: Session) -> None:
    user = add_user(db_session, linked_at=NOW - timedelta(hours=1))
    add_subscription(db_session, user, active_since=NOW - timedelta(days=1))
    add_clustered_vacancy(db_session, first_seen_at=NOW - timedelta(hours=2))

    assert match_new(db_session, now=NOW) == 0


def test_nothing_first_seen_before_the_subscription_became_active(db_session: Session) -> None:
    # Created or resumed an hour ago: a vacancy from two hours ago is not sent.
    add_subscription(db_session, add_user(db_session), active_since=NOW - timedelta(hours=1))
    add_clustered_vacancy(db_session, first_seen_at=NOW - timedelta(hours=2))

    assert match_new(db_session, now=NOW) == 0


def test_nothing_for_a_paused_subscription(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session), is_active=False)
    add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == 0


def test_nothing_for_an_account_without_a_chat(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session, chat_id=None))
    add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == 0


def test_nothing_older_than_the_lookback(db_session: Session) -> None:
    user = add_user(db_session, linked_at=NOW - LOOKBACK * 2)
    add_subscription(db_session, user, active_since=NOW - LOOKBACK * 2)
    add_clustered_vacancy(db_session, first_seen_at=NOW - LOOKBACK - timedelta(minutes=1))
    inside = add_clustered_vacancy(db_session, first_seen_at=NOW - LOOKBACK + timedelta(minutes=1))

    assert match_new(db_session, now=NOW) == 1
    assert [row[2] for row in queued(db_session)] == [inside.id]


def test_a_vacancy_without_a_cluster_waits(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session))
    add_vacancy(db_session)

    assert match_new(db_session, now=NOW) == 0


def test_an_unconverted_salary_waits_for_conversion(db_session: Session) -> None:
    # 120 000 UAH a month is above $2000; read as "no salary" before NBU
    # converts it, it would fail a subscription that skips unsalaried vacancies.
    add_subscription(
        db_session, add_user(db_session), min_salary_usd=2000, include_unsalaried=False
    )
    vacancy = add_clustered_vacancy(
        db_session,
        normalized_at=NOW - timedelta(minutes=5),
        salary_min=120_000,
        salary_currency="UAH",
        salary_period="month",
    )

    assert match_new(db_session, now=NOW) == 0

    vacancy.salary_usd_min = 2900
    db_session.flush()
    assert match_new(db_session, now=NOW) == 1


def test_a_salary_never_converted_is_matched_as_unknown_after_the_grace(
    db_session: Session,
) -> None:
    add_subscription(db_session, add_user(db_session), min_salary_usd=2000, include_unsalaried=True)
    add_clustered_vacancy(
        db_session,
        normalized_at=NOW - CONVERSION_GRACE - timedelta(minutes=1),
        salary_min=120_000,
        salary_currency="UAH",
        salary_period="month",
    )

    assert match_new(db_session, now=NOW) == 1


def test_an_hourly_salary_does_not_wait(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session))
    add_clustered_vacancy(
        db_session,
        normalized_at=NOW - timedelta(minutes=5),
        salary_min=40,
        salary_currency="USD",
        salary_period="hour",
    )

    assert match_new(db_session, now=NOW) == 1


def test_the_watermark_is_the_later_of_both_times(db_session: Session) -> None:
    user = add_user(db_session, linked_at=LINKED_AT)
    add_subscription(db_session, user, active_since=LINKED_AT - timedelta(days=1))
    before = add_clustered_vacancy(db_session, first_seen_at=LINKED_AT - timedelta(minutes=1))
    after = add_clustered_vacancy(db_session, first_seen_at=LINKED_AT + timedelta(minutes=1))

    match_new(db_session, now=NOW)

    assert [row[2] for row in queued(db_session)] == [after.id]
    assert before.id not in [row[2] for row in queued(db_session)]
