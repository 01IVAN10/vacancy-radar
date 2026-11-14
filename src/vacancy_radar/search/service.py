"""Find jobs for the search page: full-text search and filters, one result per job.

A job is a cluster (`clusters/service.py`), so a vacancy posted on Djinni
and DOU is one result with a link to each posting. A cluster is found when
one of its vacancies passes the search: it has the words (`db/fts.py`) and
every filter holds for it. That vacancy then shows the job: the best ranked
one, then the most complete (`clusters.showing.fullest_first`). A vacancy
without a cluster yet is not shown; `notify.match` clusters new vacancies
every few minutes.

Filters are strict (`domain/search.py`):

- skills: `skills && :wanted` for any, `skills @> :wanted` for all;
- levels and work formats: the vacancy states at least one of them;
- salary: the top of its monthly dollar range reaches the minimum, as in
  subscription matching (`UsdRange.top`);
- period: the job was first seen within the last days (the cluster's
  `first_seen_at`, so a repost does not make an old job new);
- sources: the vacancy is from one of them.

Results are ranked by relevance (title words weigh most) or by when the job
was first seen, newest first. Each page reads the excerpt and the postings
of its own results only.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, literal, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.clusters.showing import card_of, fullest_first
from vacancy_radar.db.fts import headline, rank, text_query
from vacancy_radar.db.models import Source, Vacancy, VacancyCluster
from vacancy_radar.domain.card import VacancyCard
from vacancy_radar.domain.search import MAX_PAGE, PAGE_SIZE, SearchQuery
from vacancy_radar.domain.snippet import excerpt, snippet_html


@dataclass(frozen=True, slots=True)
class Posting:
    """One of the job's vacancies, as a link to the original."""

    source_name: str
    url: str


@dataclass(frozen=True, slots=True)
class JobResult:
    """One job on a results page."""

    card: VacancyCard
    # Every vacancy of the job, the shown one included, by source slug then age.
    postings: tuple[Posting, ...]
    first_seen_at: datetime
    # A short excerpt of the shown vacancy's description, as safe HTML.
    snippet_html: str


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One page of results, and how many jobs the search found in all."""

    query: SearchQuery
    results: tuple[JobResult, ...]
    total: int

    @property
    def pages(self) -> int:
        """Pages that can be shown: the last ones past `MAX_PAGE` are not."""
        return min(-(-self.total // PAGE_SIZE), MAX_PAGE)


async def search_jobs(session: AsyncSession, query: SearchQuery, *, now: datetime) -> SearchPage:
    """The page `query.page` of the jobs that `query` finds."""
    tsquery = text_query(query.text) if query.text else None
    score: ColumnElement[float] = (
        rank(Vacancy.search_vector, tsquery) if tsquery is not None else literal(0.0)
    )
    # One row per cluster: the vacancy that shows it.
    best = (
        select(
            Vacancy.id,
            Vacancy.cluster_id,
            VacancyCluster.first_seen_at,
            score.label("score"),
        )
        .join(VacancyCluster, VacancyCluster.id == Vacancy.cluster_id)
        .where(*_conditions(query, tsquery, now))
        .ext(distinct_on(Vacancy.cluster_id))
        .order_by(Vacancy.cluster_id, score.desc(), *fullest_first())
        .subquery("best")
    )
    newest = (best.c.first_seen_at.desc(), best.c.cluster_id.desc())
    order = (best.c.score.desc(), *newest) if query.sort == "relevance" else newest
    rows = (
        await session.execute(
            select(best.c.id, best.c.cluster_id, func.count().over().label("total"))
            .order_by(*order)
            .limit(PAGE_SIZE)
            .offset(query.offset)
        )
    ).all()
    if rows:
        total = rows[0].total
    else:
        # Past the last page there is no row to carry the count.
        total = await session.scalar(select(func.count()).select_from(best)) or 0
    results = await _results(session, [row.id for row in rows], tsquery)
    return SearchPage(query=query, results=results, total=total)


def _conditions(
    query: SearchQuery, tsquery: ColumnElement[Any] | None, now: datetime
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if tsquery is not None:
        conditions.append(Vacancy.search_vector.bool_op("@@")(tsquery))
    if query.skills:
        wanted = list(query.skills)
        conditions.append(
            Vacancy.skills.contains(wanted)
            if query.skills_match == "all"
            else Vacancy.skills.overlap(wanted)
        )
    if query.seniority:
        conditions.append(Vacancy.seniority.overlap(list(query.seniority)))
    if query.work_formats:
        conditions.append(Vacancy.work_formats.overlap(list(query.work_formats)))
    if query.min_salary_usd is not None:
        top = func.coalesce(Vacancy.salary_usd_max, Vacancy.salary_usd_min)
        conditions.append(top >= query.min_salary_usd)
    if query.period_days is not None:
        since = now - timedelta(days=query.period_days)
        conditions.append(VacancyCluster.first_seen_at >= since)
    if query.sources:
        conditions.append(
            Vacancy.source_id.in_(select(Source.id).where(Source.slug.in_(query.sources)))
        )
    return conditions


async def _results(
    session: AsyncSession, ids: Sequence[int], tsquery: ColumnElement[Any] | None
) -> tuple[JobResult, ...]:
    """The results for the shown vacancies `ids`, in that order."""
    if not ids:
        return ()
    fragment = headline(Vacancy.description, tsquery) if tsquery is not None else literal(None)
    shown = (
        select(Vacancy, Source.name, VacancyCluster.first_seen_at, fragment.label("fragment"))
        .join(Source, Source.id == Vacancy.source_id)
        .join(VacancyCluster, VacancyCluster.id == Vacancy.cluster_id)
        .where(Vacancy.id.in_(ids))
    )
    by_id = {row.Vacancy.id: row for row in await session.execute(shown)}
    postings = await _postings(session, [row.Vacancy.cluster_id for row in by_id.values()])
    return tuple(
        JobResult(
            card=card_of(row.Vacancy, row.name),
            postings=postings[row.Vacancy.cluster_id],
            first_seen_at=row.first_seen_at,
            snippet_html=snippet_html(
                row.fragment if row.fragment is not None else excerpt(row.Vacancy.description)
            ),
        )
        for row in (by_id[vacancy_id] for vacancy_id in ids)
    )


async def _postings(
    session: AsyncSession, cluster_ids: Sequence[int | None]
) -> dict[int | None, tuple[Posting, ...]]:
    found: defaultdict[int | None, list[Posting]] = defaultdict(list)
    for cluster_id, source_name, url in await session.execute(
        select(Vacancy.cluster_id, Source.name, Vacancy.url)
        .join(Source, Source.id == Vacancy.source_id)
        .where(Vacancy.cluster_id.in_(cluster_ids))
        .order_by(Source.slug, Vacancy.id)
    ):
        found[cluster_id].append(Posting(source_name, url))
    return {cluster_id: tuple(postings) for cluster_id, postings in found.items()}


async def source_names(session: AsyncSession) -> dict[str, str]:
    """Every source's name by its slug, in slug order: what the source filter offers."""
    rows = await session.execute(select(Source.slug, Source.name).order_by(Source.slug))
    return dict(rows.all())
