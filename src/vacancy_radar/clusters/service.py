"""Which vacancies are the same job: every new vacancy joins a cluster or opens one.

Everything downstream is keyed by cluster, not by vacancy, so a job posted on
two sources, or reposted, is sent and counted once. Whether two vacancies are
the same job is `domain.dedup`; `docs/deduplication.md` has the rule and its
measurements. This module applies it under the stage 8 decisions (PLAN.md):

- **Attach-only.** A vacancy without a cluster joins the cluster of the same
  job or opens one. Clusters are never merged, and a vacancy's `cluster_id`
  never changes, re-normalization included: a new cluster would mean a new
  delivery, a repeat send.
- **One run at a time.** A run holds a transaction-level advisory lock until
  it commits, and a run that finds it taken changes nothing. Two runs at once
  could otherwise open two clusters for the two halves of one pair.
- **First seen first.** New vacancies are taken one at a time, in the order
  their raw items were first seen, so the later half of a pair that arrives
  in the same run finds the earlier one. A run takes at most `MAX_PER_RUN`,
  so a backlog (a rebuild from raw data) is worked off in short transactions.
- **Candidates.** A new vacancy is compared only with the clusters of
  vacancies whose titles share enough trigrams with its own (`title % :title`
  at pg_trgm's default threshold of 0.3, served by a GIN index) and that were
  first seen within `CANDIDATE_WINDOW` of it. `domain.dedup.pick_cluster`
  then compares it with every member of those clusters. A run reads each
  vacancy's facts once, however many new vacancies it is a candidate for.

A cluster's `first_seen_at` is the earliest first sighting of its members.
Matching takes it as the time the job appeared (`notify/matching.py`).
"""

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import batched
from typing import Final

import structlog
from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, load_only

from vacancy_radar.db import locks
from vacancy_radar.db.models import RawVacancy, Source, Vacancy, VacancyCluster
from vacancy_radar.domain.dedup import DedupFacts, pick_cluster, shingles
from vacancy_radar.domain.salary import Salary

logger = structlog.get_logger("vacancy_radar.clusters")

# How far apart two postings of one job may be first seen. An assumption, not
# a measurement: the 2026-11-05..09 sample is four days long, and the longest
# gap in it (91 hours) says more about the sample than about reposts. To be
# measured again on a longer sample (PLAN.md, findings PR). A posting further
# from its twin opens a cluster of its own and is sent again as a new job.
CANDIDATE_WINDOW: Final = timedelta(days=30)
# New vacancies one run clusters. Bounds a run's transaction, the time it holds
# the lock, and the facts it keeps in memory (about 30 KB each).
MAX_PER_RUN: Final = 1000
# Ids per `IN (...)` list when facts are read.
LOAD_BATCH: Final = 1000


@dataclass(frozen=True, slots=True)
class Clustered:
    """What a run did. A run that assigned `MAX_PER_RUN` may have left more."""

    # Vacancies that matched no cluster and opened their own.
    opened: int = 0
    # Vacancies that joined the cluster of the same job.
    joined: int = 0

    @property
    def assigned(self) -> int:
        return self.opened + self.joined


@dataclass(frozen=True, slots=True)
class Busy:
    """Another run held the lock, so this one changed nothing."""


def assign_clusters(session: Session, *, limit: int = MAX_PER_RUN) -> Clustered | Busy:
    """Give up to `limit` vacancies without a cluster one, and commit.

    The lock is taken in the session's transaction and released by the
    commit, so call this with no other work pending in the session. When the
    lock is taken, logs `clusters.busy` and returns `Busy` without committing.
    """
    if not session.scalar(select(func.pg_try_advisory_xact_lock(locks.CLUSTER, 0))):
        logger.info("clusters.busy")
        return Busy()
    pending = session.execute(
        select(Vacancy.id, Vacancy.title, RawVacancy.first_seen_at)
        .join(RawVacancy, RawVacancy.id == Vacancy.raw_vacancy_id)
        .where(Vacancy.cluster_id.is_(None))
        .order_by(RawVacancy.first_seen_at, Vacancy.id)
        .limit(limit)
    ).all()
    facts = _load_facts(session, [vacancy_id for vacancy_id, _, _ in pending])
    opened = joined = 0
    for vacancy_id, title, first_seen_at in pending:
        members = _candidate_members(session, title=title, first_seen_at=first_seen_at)
        facts |= _load_facts(session, {m for ids in members.values() for m in ids} - facts.keys())
        cluster_id = pick_cluster(
            facts[vacancy_id],
            {cluster: [facts[member] for member in ids] for cluster, ids in members.items()},
        )
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
    session.commit()
    return Clustered(opened=opened, joined=joined)


def _candidate_members(
    session: Session, *, title: str, first_seen_at: datetime
) -> dict[int, list[int]]:
    """The members of every cluster that has a vacancy like the new one, by cluster id.

    "Like" means a title that shares enough trigrams with `title` and a first
    sighting within `CANDIDATE_WINDOW` of `first_seen_at`.
    """
    similar = (
        select(Vacancy.cluster_id)
        .join(RawVacancy, RawVacancy.id == Vacancy.raw_vacancy_id)
        .where(
            Vacancy.title.op("%")(title),
            RawVacancy.first_seen_at.between(
                first_seen_at - CANDIDATE_WINDOW, first_seen_at + CANDIDATE_WINDOW
            ),
        )
    )
    members: defaultdict[int, list[int]] = defaultdict(list)
    for vacancy_id, cluster_id in session.execute(
        select(Vacancy.id, VacancyCluster.id)
        .join(VacancyCluster, VacancyCluster.id == Vacancy.cluster_id)
        .where(VacancyCluster.id.in_(similar))
        .order_by(VacancyCluster.id, Vacancy.id)
    ):
        members[cluster_id].append(vacancy_id)
    return members


def _load_facts(session: Session, ids: Collection[int]) -> dict[int, DedupFacts]:
    """What deduplication reads from each of the vacancies, by vacancy id."""
    facts: dict[int, DedupFacts] = {}
    for batch in batched(sorted(ids), LOAD_BATCH):
        rows = session.execute(
            select(Vacancy, Source.slug)
            .join(Source, Source.id == Vacancy.source_id)
            .where(Vacancy.id.in_(batch))
            .options(
                load_only(
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
            )
        ).all()
        for vacancy, slug in rows:
            facts[vacancy.id] = DedupFacts.of(
                source=slug,
                title=vacancy.title,
                company=vacancy.company,
                cities=vacancy.cities,
                seniority=vacancy.seniority,
                salary=Salary.from_stored(
                    vacancy.salary_min,
                    vacancy.salary_max,
                    vacancy.salary_currency,
                    vacancy.salary_period,
                ),
                shingles=shingles(vacancy.description),
            )
    return facts
