"""Which vacancies are the same job. For now, every vacancy is its own cluster.

Everything downstream is keyed by cluster, not by vacancy, so that a job
posted on two sources is sent and counted once. Deduplication (stage 8)
replaces the rule here; the code that reads clusters does not change.
"""

from typing import Final

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from vacancy_radar.db.models import RawVacancy, Vacancy, VacancyCluster

BATCH_SIZE: Final = 500


def assign_clusters(session: Session, *, batch_size: int = BATCH_SIZE) -> int:
    """Give every vacancy without a cluster one of its own. Returns how many. Does not commit.

    A cluster's `first_seen_at` is its vacancy's raw `first_seen_at`. Rows are
    locked with SKIP LOCKED, so two runs at once never cluster a vacancy twice.
    """
    assigned = 0
    while True:
        batch = session.execute(
            select(Vacancy.id, RawVacancy.first_seen_at)
            .join(RawVacancy, RawVacancy.id == Vacancy.raw_vacancy_id)
            .where(Vacancy.cluster_id.is_(None))
            .order_by(Vacancy.id)
            .limit(batch_size)
            .with_for_update(of=Vacancy, skip_locked=True)
        ).all()
        if not batch:
            return assigned
        cluster_ids = session.scalars(
            insert(VacancyCluster).returning(VacancyCluster.id, sort_by_parameter_order=True),
            [{"first_seen_at": row.first_seen_at} for row in batch],
        ).all()
        session.execute(
            update(Vacancy),
            [
                {"id": row.id, "cluster_id": cluster_id}
                for row, cluster_id in zip(batch, cluster_ids, strict=True)
            ],
        )
        assigned += len(batch)
