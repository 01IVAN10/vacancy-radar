"""Recording matches and queuing deliveries: who gets which cluster, and only once."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tests.notify.factories import (
    LINKED_AT,
    NOW,
    add_clustered_vacancy,
    add_subscription,
    add_user,
    add_vacancy,
)
from vacancy_radar.db.models import Delivery, Notification, Subscription, Vacancy
from vacancy_radar.notify import matching
from vacancy_radar.notify.matching import CONVERSION_GRACE, LOOKBACK, MatchResult, match_new


def queued(session: Session) -> list[tuple[int, int, int, str]]:
    """(subscription, cluster, vacancy, delivery status) per recorded match."""
    rows = session.execute(
        select(
            Notification.subscription_id,
            Notification.cluster_id,
            Vacancy.id,
            Delivery.status,
        )
        .join(Delivery, Delivery.id == Notification.delivery_id)
        .join(Vacancy, Vacancy.cluster_id == Notification.cluster_id)
        .order_by(Notification.id)
    )
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def deliveries(session: Session) -> list[Delivery]:
    return list(session.scalars(select(Delivery).order_by(Delivery.id)))


def test_queues_a_new_matching_cluster(db_session: Session) -> None:
    subscription = add_subscription(db_session, add_user(db_session))
    vacancy = add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == MatchResult(matched=1, queued=1)

    assert vacancy.cluster_id is not None
    assert queued(db_session) == [(subscription.id, vacancy.cluster_id, vacancy.id, "pending")]
    [delivery] = deliveries(db_session)
    assert (delivery.next_attempt_at, delivery.attempts) == (NOW, 0)


def test_running_again_queues_nothing_new(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session))
    add_clustered_vacancy(db_session)
    match_new(db_session, now=NOW)

    assert match_new(db_session, now=NOW) == MatchResult()
    assert len(queued(db_session)) == 1


def test_a_vacancy_that_does_not_match_is_not_queued(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session), skills=["go"])
    add_clustered_vacancy(db_session, skills=["python"])

    assert match_new(db_session, now=NOW) == MatchResult()


def test_two_matching_subscriptions_share_one_delivery(db_session: Session) -> None:
    user = add_user(db_session)
    python = add_subscription(db_session, user, "Python")
    backend = add_subscription(db_session, user, "Backend", skills=["python", "django"])
    vacancy = add_clustered_vacancy(db_session, skills=["python"])

    assert match_new(db_session, now=NOW) == MatchResult(matched=2, queued=1)
    assert {row[0] for row in queued(db_session)} == {python.id, backend.id}
    assert {row[1] for row in queued(db_session)} == {vacancy.cluster_id}
    assert len(deliveries(db_session)) == 1


def test_a_subscription_edited_to_match_after_the_message_sends_nothing_new(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    add_subscription(db_session, user, "Python")
    django = add_subscription(db_session, user, "Django", skills=["django"])
    add_clustered_vacancy(db_session, skills=["python"])
    match_new(db_session, now=NOW)
    db_session.execute(update(Delivery).values(status="sent", sent_at=NOW))

    db_session.execute(
        update(Subscription).where(Subscription.id == django.id).values(skills=["python"])
    )
    result = match_new(db_session, now=NOW + timedelta(minutes=5))

    # The match is recorded on the delivery already sent; no new message.
    assert result == MatchResult(matched=1, queued=0)
    assert [delivery.status for delivery in deliveries(db_session)] == ["sent"]


def test_a_vacancy_renormalized_to_match_another_subscription_sends_nothing_new(
    db_session: Session,
) -> None:
    user = add_user(db_session)
    add_subscription(db_session, user, "Python")
    add_subscription(db_session, user, "Go", skills=["go"])
    vacancy = add_clustered_vacancy(db_session, skills=["python"])
    match_new(db_session, now=NOW)

    db_session.execute(update(Vacancy).where(Vacancy.id == vacancy.id).values(skills=["go"]))

    assert match_new(db_session, now=NOW) == MatchResult(matched=1, queued=0)
    assert len(deliveries(db_session)) == 1


def test_each_account_gets_its_own_delivery(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session, "first@example.com", chat_id=1))
    add_subscription(db_session, add_user(db_session, "second@example.com", chat_id=2))
    add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == MatchResult(matched=2, queued=2)


def test_inserts_in_batches(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matching, "BATCH_SIZE", 2)
    user = add_user(db_session)
    add_subscription(db_session, user, "Python")
    add_subscription(db_session, user, "Backend", skills=["python", "django"])
    for _ in range(3):
        add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == MatchResult(matched=6, queued=3)


def test_nothing_first_seen_before_the_chat_was_linked(db_session: Session) -> None:
    user = add_user(db_session, linked_at=NOW - timedelta(hours=1))
    add_subscription(db_session, user, active_since=NOW - timedelta(days=1))
    add_clustered_vacancy(db_session, first_seen_at=NOW - timedelta(hours=2))

    assert match_new(db_session, now=NOW) == MatchResult()


def test_nothing_first_seen_before_the_subscription_became_active(db_session: Session) -> None:
    # Created or resumed an hour ago: a vacancy from two hours ago is not sent.
    add_subscription(db_session, add_user(db_session), active_since=NOW - timedelta(hours=1))
    add_clustered_vacancy(db_session, first_seen_at=NOW - timedelta(hours=2))

    assert match_new(db_session, now=NOW) == MatchResult()


def test_nothing_for_a_paused_subscription(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session), is_active=False)
    add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == MatchResult()


def test_nothing_for_an_account_without_a_chat(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session, chat_id=None))
    add_clustered_vacancy(db_session)

    assert match_new(db_session, now=NOW) == MatchResult()


def test_nothing_older_than_the_lookback(db_session: Session) -> None:
    user = add_user(db_session, linked_at=NOW - LOOKBACK * 2)
    add_subscription(db_session, user, active_since=NOW - LOOKBACK * 2)
    add_clustered_vacancy(db_session, first_seen_at=NOW - LOOKBACK - timedelta(minutes=1))
    inside = add_clustered_vacancy(db_session, first_seen_at=NOW - LOOKBACK + timedelta(minutes=1))

    assert match_new(db_session, now=NOW).queued == 1
    assert [row[2] for row in queued(db_session)] == [inside.id]


def test_a_new_vacancy_of_an_older_cluster_is_matched_on_its_own_facts(
    db_session: Session,
) -> None:
    """The lookback counts from each vacancy's first sighting, not the cluster's."""
    user = add_user(db_session, linked_at=NOW - LOOKBACK * 3)
    add_subscription(db_session, user, active_since=NOW - LOOKBACK * 3, skills=["go"])
    first = add_clustered_vacancy(db_session, first_seen_at=NOW - LOOKBACK * 2, skills=["java"])
    later = add_vacancy(
        db_session,
        cluster_id=first.cluster_id,
        first_seen_at=NOW - timedelta(hours=1),
        skills=["go"],
    )

    assert match_new(db_session, now=NOW) == MatchResult(matched=1, queued=1)
    assert first.cluster_id == later.cluster_id
    assert [row[1] for row in queued(db_session)] == [first.cluster_id, first.cluster_id]


def test_a_new_vacancy_of_a_cluster_older_than_the_subscription_is_not_matched(
    db_session: Session,
) -> None:
    """The watermark stays on the cluster: the job appeared before the subscription did."""
    user = add_user(db_session, linked_at=NOW - LOOKBACK * 3)
    add_subscription(db_session, user, active_since=NOW - LOOKBACK, skills=["go"])
    first = add_clustered_vacancy(db_session, first_seen_at=NOW - LOOKBACK * 2, skills=["java"])
    add_vacancy(
        db_session,
        cluster_id=first.cluster_id,
        first_seen_at=NOW - timedelta(hours=1),
        skills=["go"],
    )

    assert match_new(db_session, now=NOW) == MatchResult()


def test_a_vacancy_without_a_cluster_waits(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session))
    add_vacancy(db_session)

    assert match_new(db_session, now=NOW) == MatchResult()


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

    assert match_new(db_session, now=NOW) == MatchResult()

    vacancy.salary_usd_min = 2900
    db_session.flush()
    assert match_new(db_session, now=NOW).queued == 1


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

    assert match_new(db_session, now=NOW).queued == 1


@pytest.mark.parametrize(
    "salary",
    [
        {},
        {"salary_min": 4000, "salary_currency": "USD", "salary_period": "month"},
        {
            "salary_min": 3500,
            "salary_currency": "EUR",
            "salary_period": "month",
            "salary_usd_min": 3800,
        },
    ],
    ids=["no salary", "dollars", "converted"],
)
def test_a_salary_that_needs_no_conversion_does_not_wait(
    db_session: Session, salary: dict[str, object]
) -> None:
    add_subscription(db_session, add_user(db_session))
    add_clustered_vacancy(db_session, normalized_at=NOW - timedelta(minutes=5), **salary)

    assert match_new(db_session, now=NOW).queued == 1


def test_an_hourly_salary_does_not_wait(db_session: Session) -> None:
    add_subscription(db_session, add_user(db_session))
    add_clustered_vacancy(
        db_session,
        normalized_at=NOW - timedelta(minutes=5),
        salary_min=40,
        salary_currency="USD",
        salary_period="hour",
    )

    assert match_new(db_session, now=NOW).queued == 1


def test_the_watermark_is_the_later_of_both_times(db_session: Session) -> None:
    user = add_user(db_session, linked_at=LINKED_AT)
    add_subscription(db_session, user, active_since=LINKED_AT - timedelta(days=1))
    before = add_clustered_vacancy(db_session, first_seen_at=LINKED_AT - timedelta(minutes=1))
    after = add_clustered_vacancy(db_session, first_seen_at=LINKED_AT + timedelta(minutes=1))

    match_new(db_session, now=NOW)

    assert [row[2] for row in queued(db_session)] == [after.id]
    assert before.id not in [row[2] for row in queued(db_session)]
