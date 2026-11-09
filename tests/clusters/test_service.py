"""Cluster assignment against the test database: the rule of `domain.dedup`, applied."""

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from tests.notify.factories import JOB_TEXT, NOW, add_vacancy
from tests.support import fixture_bytes
from vacancy_radar.clusters.service import CANDIDATE_WINDOW, Busy, Clustered, assign_clusters
from vacancy_radar.collect.service import ensure_source, upsert_raw_items
from vacancy_radar.db import locks
from vacancy_radar.db.models import Vacancy, VacancyCluster
from vacancy_radar.domain.normalize import normalize
from vacancy_radar.normalize.service import normalize_pending
from vacancy_radar.sources import ADAPTERS, djinni

# A description that shares nothing with JOB_TEXT.
OTHER_TEXT = " ".join(f"other{n}" for n in range(60))


def posting(session: Session, **fields: Any) -> Vacancy:
    """A vacancy with the shared text and no company, as Djinni posts it, first seen 2 h ago."""
    defaults: dict[str, Any] = {"company": None, "description": JOB_TEXT}
    return add_vacancy(session, **(defaults | fields))


def cluster_of(session: Session, vacancy: Vacancy) -> VacancyCluster:
    session.refresh(vacancy)
    assert vacancy.cluster_id is not None
    return session.get_one(VacancyCluster, vacancy.cluster_id)


def cluster_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(VacancyCluster)) or 0


def test_different_jobs_open_clusters_of_their_own(db_session: Session) -> None:
    first = posting(db_session, first_seen_at=NOW - timedelta(hours=3))
    second = posting(db_session, title="Go Developer", first_seen_at=NOW - timedelta(hours=1))

    assert assign_clusters(db_session) == Clustered(opened=2)

    # First seen when the raw item was, not when the cluster was created.
    assert cluster_of(db_session, first).first_seen_at == NOW - timedelta(hours=3)
    assert cluster_of(db_session, second).first_seen_at == NOW - timedelta(hours=1)


def test_the_same_job_on_another_source_joins_the_cluster(db_session: Session) -> None:
    djinni_posting = posting(db_session, first_seen_at=NOW - timedelta(hours=3))
    assign_clusters(db_session)
    dou_posting = posting(
        db_session, source="dou", title="Python Developer (Fintech)", company="Acme"
    )

    assert assign_clusters(db_session) == Clustered(joined=1)

    cluster = cluster_of(db_session, djinni_posting)
    assert cluster_of(db_session, dou_posting) == cluster
    # The cluster keeps its first sighting, so the new member sends nothing.
    assert cluster.first_seen_at == NOW - timedelta(hours=3)


def test_a_repost_joins_the_cluster(db_session: Session) -> None:
    original = posting(db_session, first_seen_at=NOW - timedelta(days=3))
    repost = posting(db_session)

    assert assign_clusters(db_session) == Clustered(opened=1, joined=1)
    assert cluster_of(db_session, repost) == cluster_of(db_session, original)


def test_another_role_with_the_same_text_opens_its_own_cluster(db_session: Session) -> None:
    posting(db_session, title="Retention Manager")
    posting(db_session, title="Retention Specialist")

    assert assign_clusters(db_session) == Clustered(opened=2)


def test_the_same_title_with_another_text_opens_its_own_cluster(db_session: Session) -> None:
    posting(db_session)
    posting(db_session, description=OTHER_TEXT)

    assert assign_clusters(db_session) == Clustered(opened=2)


def test_the_earlier_half_of_a_pair_opens_the_cluster(db_session: Session) -> None:
    # Normalized in the other order, as when one source is collected late.
    later = posting(db_session, source="dou", first_seen_at=NOW - timedelta(hours=1))
    earlier = posting(db_session, first_seen_at=NOW - timedelta(hours=5))

    assert assign_clusters(db_session) == Clustered(opened=1, joined=1)

    cluster = cluster_of(db_session, later)
    assert cluster_of(db_session, earlier) == cluster
    assert cluster.first_seen_at == NOW - timedelta(hours=5)


def test_an_earlier_member_moves_the_first_sighting_back(db_session: Session) -> None:
    first = posting(db_session, first_seen_at=NOW - timedelta(hours=1))
    assign_clusters(db_session)
    posting(db_session, source="dou", first_seen_at=NOW - timedelta(hours=4))

    assert assign_clusters(db_session) == Clustered(joined=1)
    assert cluster_of(db_session, first).first_seen_at == NOW - timedelta(hours=4)


def test_joins_only_the_cluster_of_every_member(db_session: Session) -> None:
    """Django ~ plain and plain ~ Flask, but Django ≁ Flask on one source."""
    plain = posting(db_session, title="Python Developer")
    django = posting(db_session, source="dou", title="Python Developer (Django)")
    assert assign_clusters(db_session) == Clustered(opened=1, joined=1)
    flask = posting(db_session, source="dou", title="Python Developer (Flask)")

    assert assign_clusters(db_session) == Clustered(opened=1)
    assert cluster_of(db_session, django) == cluster_of(db_session, plain)
    assert cluster_of(db_session, flask) != cluster_of(db_session, plain)


def test_postings_further_apart_than_the_window_are_two_jobs(db_session: Session) -> None:
    posting(db_session, first_seen_at=NOW - CANDIDATE_WINDOW - timedelta(days=1))
    posting(db_session, first_seen_at=NOW)

    assert assign_clusters(db_session) == Clustered(opened=2)


def test_assigning_again_changes_nothing(db_session: Session) -> None:
    posting(db_session)
    assign_clusters(db_session)

    assert assign_clusters(db_session) == Clustered()
    assert cluster_count(db_session) == 1


def test_a_run_waits_for_nothing_while_another_holds_the_lock(
    db_session: Session, migrated_engine: Engine
) -> None:
    vacancy = posting(db_session)

    with migrated_engine.connect() as other:
        other.execute(select(func.pg_advisory_lock(locks.CLUSTER, 0)))
        try:
            result = assign_clusters(db_session)
        finally:
            other.execute(select(func.pg_advisory_unlock(locks.CLUSTER, 0)))

    assert result == Busy()
    db_session.refresh(vacancy)
    assert vacancy.cluster_id is None


def test_normalizing_again_keeps_the_cluster(db_session: Session) -> None:
    items = djinni.parse_feed(fixture_bytes("djinni/rss_all.xml")).items
    upsert_raw_items(db_session, ensure_source(db_session, ADAPTERS["djinni"]), items)
    normalize_pending(db_session)
    assign_clusters(db_session)
    before = dict(db_session.execute(select(Vacancy.id, Vacancy.cluster_id)).all())

    normalize_pending(db_session, rebuild=True)

    db_session.expire_all()
    after = dict(db_session.execute(select(Vacancy.id, Vacancy.cluster_id)).all())
    assert after == before
    assert None not in after.values()


def test_a_run_takes_at_most_its_limit_in_first_seen_order(db_session: Session) -> None:
    earlier = posting(db_session, first_seen_at=NOW - timedelta(hours=3))
    later = posting(db_session, source="dou")

    assert assign_clusters(db_session, limit=1) == Clustered(opened=1)
    db_session.refresh(later)
    assert later.cluster_id is None
    assert assign_clusters(db_session, limit=1) == Clustered(joined=1)
    assert cluster_of(db_session, later) == cluster_of(db_session, earlier)


# --- candidates -----------------------------------------------------------------------


def test_a_dissimilar_title_is_never_compared(db_session: Session) -> None:
    """The rule would take these for one job; the trigram search never offers it the pair."""
    posting(db_session, title="Python Developer")
    posting(db_session, source="dou", title="Python Developer at a very long company name")

    assert assign_clusters(db_session) == Clustered(opened=1, joined=1)

    posting(db_session, title="QA")
    posting(db_session, source="dou", title="QA (Odoo, ERP, CRM, Salesforce, HubSpot)")
    assert assign_clusters(db_session) == Clustered(opened=2)


def test_the_title_search_can_use_the_trigram_index(db_session: Session) -> None:
    db_session.execute(text("SET LOCAL enable_seqscan = off"))

    plan = db_session.scalars(
        text("EXPLAIN SELECT id FROM vacancy WHERE title % :title"), {"title": "Python"}
    ).all()

    assert any("ix_vacancy_title_trgm" in line for line in plan), plan


def test_every_duplicate_pair_of_the_sample_shares_a_cluster(db_session: Session) -> None:
    """The trigram search and the rule together keep every real pair together.

    The pairs are those of `tests/fixtures/dedup/pairs.json` that are one job,
    the known miss included. The fixture keeps no description, so each pair
    gets a text of its own; the titles, companies, places and levels are the
    real ones, normalized from the feed titles as the fixture test does.
    """
    fixture = json.loads(fixture_bytes("dedup/pairs.json"))

    def add(key: str, description: str) -> Vacancy:
        stored = fixture["vacancies"][key]
        vacancy = normalize(stored["source"], {"title": stored["feed_title"]})
        salary = vacancy.salary
        return posting(
            db_session,
            source=stored["source"],
            title=vacancy.title,
            company=vacancy.company,
            cities=list(vacancy.cities),
            seniority=list(vacancy.seniority),
            salary_min=salary.min_amount if salary else None,
            salary_max=salary.max_amount if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period if salary else None,
            description=description,
        )

    pairs = [
        (add(pair["a"], text), add(pair["b"], text), pair)
        for number, pair in enumerate(fixture["pairs"])
        if pair["expected"] == "same"
        for text in [" ".join(f"pair{number}w{n}" for n in range(60))]
    ]
    assign_clusters(db_session)

    apart = [
        (pair["a"], pair["b"])
        for a, b, pair in pairs
        if cluster_of(db_session, a) != cluster_of(db_session, b)
    ]
    assert apart == []
    assert len(pairs) == 14
