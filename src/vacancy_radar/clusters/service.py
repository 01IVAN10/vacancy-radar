"""Which vacancies are the same job: every new vacancy joins a cluster or opens one.

Everything downstream is keyed by cluster, not by vacancy, so a job posted on
two sources, or reposted, is sent and counted once. Whether two vacancies are
the same job is `domain.dedup`; `docs/deduplication.md` has the rule and its
measurements. This module applies it under the stage 8 decisions (PLAN.md):

- **Attach-only.** A vacancy without a cluster joins the cluster of the same
  job or opens one. Clusters are never merged, and a vacancy's `cluster_id`
  never changes, re-normalization included: a new cluster would mean a new
  delivery, a repeat send.
- **One run at a time.** A transaction-level advisory lock covers the whole
  run, and a run that finds it taken does nothing. Two runs at once could
  otherwise open two clusters for the two halves of one pair.
- **First seen first.** New vacancies are taken one at a time, in the order
  their raw items were first seen, so the later half of a pair that arrives
  in the same run finds the earlier one.
- **Candidates.** A new vacancy is compared only with vacancies whose titles
  share enough trigrams with its own (`title % :title` at pg_trgm's default
  threshold of 0.3, served by a GIN index) and that were first seen within
  `CANDIDATE_WINDOW` of it. `domain.dedup.pick_cluster` then compares it with
  every member of their clusters.

A cluster's `first_seen_at` is the earliest first sighting of its members, so
a vacancy that joins an old cluster stays behind the matching watermark and
sends nothing: it is the same job.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Final

from sqlalchemy import ColumnElement, Row, func, insert, select, update
from sqlalchemy.orm import Session

from vacancy_radar.db import locks
from vacancy_radar.db.models import RawVacancy, Source, Vacancy, VacancyCluster
from vacancy_radar.domain.dedup import DedupFacts, pick_cluster, shingles
from vacancy_radar.domain.salary import Salary

# A row of `_fact_rows`. SQLAlchemy types only the first ten columns of a select.
type FactRow = Row[*tuple[Any, ...]]

# How far apart two postings of one job may be first seen. Wider than any
# repost seen so far, and it keeps each comparison to a month of vacancies.
CANDIDATE_WINDOW: Final = timedelta(days=30)
# Descriptions whose shingles one run keeps, so a vacancy that is a candidate
# for many new ones is hashed once. About 30 KB each.
SHINGLE_CACHE_SIZE: Final = 2000


@dataclass(frozen=True, slots=True)
class ClusterResult:
    # Vacancies that matched no cluster and opened their own.
    opened: int = 0
    # Vacancies that joined the cluster of the same job.
    joined: int = 0
    # Another run held the lock, so this one changed nothing.
    busy: bool = False


def assign_clusters(session: Session) -> ClusterResult:
    """Give every vacancy without a cluster one. Does not commit.

    The lock is held until the caller's transaction ends, so commit soon.
    """
    if not session.scalar(select(func.pg_try_advisory_xact_lock(locks.CLUSTER, 0))):
        return ClusterResult(busy=True)
    pending = session.execute(
        select(Vacancy.id, RawVacancy.first_seen_at)
        .join(RawVacancy, RawVacancy.id == Vacancy.raw_vacancy_id)
        .where(Vacancy.cluster_id.is_(None))
        .order_by(RawVacancy.first_seen_at, Vacancy.id)
    ).all()
    hashed = lru_cache(maxsize=SHINGLE_CACHE_SIZE)(shingles)
    opened = joined = 0
    for vacancy_id, first_seen_at in pending:
        [new] = _fact_rows(session, Vacancy.id == vacancy_id)
        cluster_id = _cluster_of(session, new, first_seen_at, hashed)
        if cluster_id is None:
            cluster_id = session.scalars(
                insert(VacancyCluster)
                .values(first_seen_at=first_seen_at)
                .returning(VacancyCluster.id)
            ).one()
            opened += 1
        else:
            session.execute(
                update(VacancyCluster)
                .where(VacancyCluster.id == cluster_id)
                .values(first_seen_at=func.least(VacancyCluster.first_seen_at, first_seen_at))
            )
            joined += 1
        session.execute(
            update(Vacancy).where(Vacancy.id == vacancy_id).values(cluster_id=cluster_id)
        )
    return ClusterResult(opened=opened, joined=joined)


def candidate_clusters(session: Session, *, title: str, first_seen_at: datetime) -> list[int]:
    """Clusters with a vacancy whose title shares enough trigrams with `title`.

    Only vacancies first seen within `CANDIDATE_WINDOW` of `first_seen_at`
    count. In id order.
    """
    found = session.scalars(
        select(Vacancy.cluster_id)
        .distinct()
        .join(RawVacancy, RawVacancy.id == Vacancy.raw_vacancy_id)
        .where(
            Vacancy.cluster_id.is_not(None),
            Vacancy.title.op("%")(title),
            RawVacancy.first_seen_at.between(
                first_seen_at - CANDIDATE_WINDOW, first_seen_at + CANDIDATE_WINDOW
            ),
        )
        .order_by(Vacancy.cluster_id)
    )
    return [cluster_id for cluster_id in found if cluster_id is not None]


def _cluster_of(
    session: Session,
    new: FactRow,
    first_seen_at: datetime,
    hashed: Callable[[str], frozenset[int]],
) -> int | None:
    """The cluster the vacancy in `new` joins, or None if it opens one."""
    candidates = candidate_clusters(session, title=new.title, first_seen_at=first_seen_at)
    if not candidates:
        return None
    members: defaultdict[int, list[DedupFacts]] = defaultdict(list)
    for member in _fact_rows(session, Vacancy.cluster_id.in_(candidates)):
        members[member.cluster_id].append(_facts(member, hashed))
    return pick_cluster(_facts(new, hashed), members)


def _fact_rows(session: Session, condition: ColumnElement[bool]) -> Sequence[FactRow]:
    """The columns deduplication reads, for the vacancies that meet `condition`."""
    return session.execute(
        select(
            Vacancy.id,
            Vacancy.cluster_id,
            Source.slug,
            Vacancy.title,
            Vacancy.company,
            Vacancy.cities,
            Vacancy.seniority,
            Vacancy.salary_min,
            Vacancy.salary_max,
            Vacancy.salary_currency,
            Vacancy.salary_period,
            Vacancy.description,
        )
        .join(Source, Source.id == Vacancy.source_id)
        .where(condition)
        .order_by(Vacancy.id)
    ).all()


def _facts(row: FactRow, hashed: Callable[[str], frozenset[int]]) -> DedupFacts:
    salary = (
        None
        if row.salary_currency is None
        else Salary(row.salary_min, row.salary_max, row.salary_currency, row.salary_period)
    )
    return DedupFacts.of(
        source=row.slug,
        title=row.title,
        company=row.company,
        cities=row.cities,
        seniority=row.seniority,
        salary=salary,
        shingles=hashed(row.description),
    )
