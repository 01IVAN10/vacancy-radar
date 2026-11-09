"""Normalize the raw items that have no up-to-date `vacancy` row, then convert salaries.

A raw item is pending when it has no vacancy yet, when its payload changed
since (`raw_vacancy.updated_at` no longer equals `vacancy.raw_updated_at`),
or when its vacancy was built by another `NORMALIZER_VERSION` (an older one,
or a newer one after a rollback). Pending items
are processed in batches, one transaction each, and written with
`INSERT … ON CONFLICT (raw_vacancy_id) DO UPDATE`, so a second run changes
nothing and two runs at once only repeat each other's work.

Normalization needs no network. A salary in dollars gets its monthly USD
range right away; one in hryvnias or euros gets it from `convert_salaries`,
which needs NBU rates. So an NBU outage leaves only those dollar columns
empty, and the next run fills them.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import ColumnElement, Row, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vacancy_radar.db.models import RawVacancy, Skill, Source, Vacancy
from vacancy_radar.domain.money import UsdRange, to_monthly_usd
from vacancy_radar.domain.normalize import (
    NORMALIZER_VERSION,
    NORMALIZERS,
    NormalizedVacancy,
    kyiv_date,
    normalize,
)
from vacancy_radar.domain.salary import Salary
from vacancy_radar.domain.skills import SKILLS
from vacancy_radar.exchange.rates import ensure_rates

BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class NormalizeStats:
    """How many vacancies one run wrote, and how many of them have a salary."""

    normalized: int = 0
    with_salary: int = 0

    def __add__(self, other: "NormalizeStats") -> "NormalizeStats":
        return NormalizeStats(
            self.normalized + other.normalized, self.with_salary + other.with_salary
        )


def normalize_pending(
    session: Session, *, rebuild: bool = False, batch_size: int = BATCH_SIZE
) -> NormalizeStats:
    """Normalize every pending raw item, or every raw item with `rebuild`. Commits."""
    sync_skills(session)
    session.commit()

    stats = NormalizeStats()
    last_id = 0
    while batch := pending_batch(session, after_id=last_id, rebuild=rebuild, limit=batch_size):
        stats += normalize_batch(session, batch)
        session.commit()
        last_id = batch[-1].id
    return stats


def needs_conversion() -> ColumnElement[bool]:
    """True for a vacancy whose salary `convert_salaries` has yet to convert to dollars.

    That is a monthly or yearly salary in hryvnias or euros without a dollar
    range. Never NULL, so its negation is safe in SQL too. Matching waits for
    these (`notify/matching.py`).
    """
    return and_(
        Vacancy.salary_currency.is_not(None),
        Vacancy.salary_currency != "USD",
        Vacancy.salary_period != "hour",
        Vacancy.salary_usd_min.is_(None),
        Vacancy.salary_usd_max.is_(None),
    )


def convert_salaries(
    session: Session, client: httpx.Client, *, sleep: Callable[[float], None] = time.sleep
) -> int:
    """Fill the monthly USD range of every salary that still lacks one. Commits.

    Returns how many vacancies got one. Errors from NBU propagate and nothing
    is written; the salaries stay unconverted for the next run. So does a
    salary whose day has no rate and no recent earlier one.
    """
    rows = session.execute(
        select(
            Vacancy.id,
            Vacancy.salary_min,
            Vacancy.salary_max,
            Vacancy.salary_currency,
            Vacancy.salary_period,
            Vacancy.published_at,
            RawVacancy.first_seen_at,
        )
        .join(RawVacancy, RawVacancy.id == Vacancy.raw_vacancy_id)
        .where(needs_conversion())
    ).all()
    if not rows:
        return 0

    rates = ensure_rates(
        session, client, {rate_day(r.published_at, r.first_seen_at) for r in rows}, sleep=sleep
    )
    converted = []
    for row in rows:
        salary = Salary(row.salary_min, row.salary_max, row.salary_currency, row.salary_period)
        usd = to_monthly_usd(salary, rates.get(rate_day(row.published_at, row.first_seen_at)))
        if usd is not None:
            converted.append({"id": row.id, **usd_columns(usd)})
    if converted:
        session.execute(update(Vacancy), converted)
    session.commit()
    return len(converted)


def sync_skills(session: Session) -> None:
    """Make the `skill` table match the dictionary in `domain/skills.py`."""
    values = insert(Skill).values(
        [{"slug": s.slug, "name": s.name, "category": s.category} for s in SKILLS]
    )
    session.execute(
        values.on_conflict_do_update(
            index_elements=[Skill.slug],
            set_={"name": values.excluded.name, "category": values.excluded.category},
        )
    )
    session.execute(delete(Skill).where(Skill.slug.not_in([s.slug for s in SKILLS])))


def pending_batch(
    session: Session, *, after_id: int, rebuild: bool, limit: int
) -> Sequence[Row[Any]]:
    """The next `limit` pending raw items with an id above `after_id`, in id order."""
    query = (
        select(
            RawVacancy.id,
            RawVacancy.source_id,
            RawVacancy.url,
            RawVacancy.payload,
            RawVacancy.updated_at,
            RawVacancy.first_seen_at,
            Source.slug,
        )
        .join(Source, Source.id == RawVacancy.source_id)
        .outerjoin(Vacancy, Vacancy.raw_vacancy_id == RawVacancy.id)
        .where(RawVacancy.id > after_id, Source.slug.in_(NORMALIZERS))
        .order_by(RawVacancy.id)
        .limit(limit)
    )
    if not rebuild:
        query = query.where(
            or_(
                Vacancy.id.is_(None),
                Vacancy.raw_updated_at.is_distinct_from(RawVacancy.updated_at),
                Vacancy.normalizer_version != NORMALIZER_VERSION,
            )
        )
    return session.execute(query).all()


def normalize_batch(session: Session, batch: Sequence[Row[Any]]) -> NormalizeStats:
    """Normalize one batch and upsert its vacancies. Does not commit."""
    values = [vacancy_values(row, normalize(row.slug, row.payload)) for row in batch]
    insert_values = insert(Vacancy).values(values)
    session.execute(
        insert_values.on_conflict_do_update(
            index_elements=[Vacancy.raw_vacancy_id],
            set_={
                **{
                    key: insert_values.excluded[key] for key in values[0] if key != "raw_vacancy_id"
                },
                "normalized_at": func.now(),
            },
        )
    )
    return NormalizeStats(
        normalized=len(values),
        with_salary=sum(v["salary_currency"] is not None for v in values),
    )


def rate_day(published_at: datetime | None, first_seen_at: datetime) -> date:
    """The Kyiv day whose rate converts the salary: publication, else first sight."""
    return kyiv_date(published_at or first_seen_at)


def usd_columns(usd: UsdRange | None) -> dict[str, int | None]:
    """The `salary_usd_*` column values of a monthly dollar range."""
    return {
        "salary_usd_min": None if usd is None else usd.min_usd,
        "salary_usd_max": None if usd is None else usd.max_usd,
    }


def vacancy_values(row: Row[Any], vacancy: NormalizedVacancy) -> dict[str, Any]:
    """Column values of the `vacancy` row for one raw item.

    Without rates, only a salary in dollars gets its USD range here; the
    others are left to `convert_salaries`.
    """
    salary = vacancy.salary
    usd = to_monthly_usd(salary, rates=None) if salary else None
    return {
        "raw_vacancy_id": row.id,
        "source_id": row.source_id,
        "url": row.url,
        "title": vacancy.title,
        "company": vacancy.company,
        "cities": list(vacancy.cities),
        "category": vacancy.category,
        "published_at": vacancy.published_at,
        "salary_min": salary.min_amount if salary else None,
        "salary_max": salary.max_amount if salary else None,
        "salary_currency": salary.currency if salary else None,
        "salary_period": salary.period if salary else None,
        **usd_columns(usd),
        "seniority": list(vacancy.seniority),
        "work_formats": list(vacancy.work_formats),
        "skills": list(vacancy.skills),
        "description": vacancy.description,
        "normalizer_version": NORMALIZER_VERSION,
        "raw_updated_at": row.updated_at,
    }
