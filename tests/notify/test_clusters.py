from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.notify.factories import NOW, add_vacancy
from tests.support import fixture_bytes
from vacancy_radar.collect.service import ensure_source, upsert_raw_items
from vacancy_radar.db.models import Vacancy, VacancyCluster
from vacancy_radar.normalize.service import normalize_pending
from vacancy_radar.notify.clusters import assign_clusters
from vacancy_radar.sources import ADAPTERS, djinni


def clusters(session: Session) -> list[VacancyCluster]:
    return list(session.scalars(select(VacancyCluster).order_by(VacancyCluster.id)))


def test_every_vacancy_gets_a_cluster_of_its_own(db_session: Session) -> None:
    first = add_vacancy(db_session, first_seen_at=NOW - timedelta(hours=3))
    second = add_vacancy(db_session, first_seen_at=NOW - timedelta(hours=1))

    assert assign_clusters(db_session, batch_size=1) == 2

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.cluster_id is not None
    assert second.cluster_id is not None
    assert first.cluster_id != second.cluster_id
    by_id = {cluster.id: cluster for cluster in clusters(db_session)}
    # First seen when the raw item was, not when the cluster was created.
    assert by_id[first.cluster_id].first_seen_at == NOW - timedelta(hours=3)
    assert by_id[second.cluster_id].first_seen_at == NOW - timedelta(hours=1)


def test_assigning_again_changes_nothing(db_session: Session) -> None:
    add_vacancy(db_session)
    assign_clusters(db_session)

    assert assign_clusters(db_session) == 0
    assert len(clusters(db_session)) == 1


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
