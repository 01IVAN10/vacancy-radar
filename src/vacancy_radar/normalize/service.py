"""Normalize the raw items that have no up-to-date `vacancy` row.

A raw item is pending when it has no vacancy yet, when its payload changed
since (`raw_vacancy.updated_at` no longer equals `vacancy.raw_updated_at`),
or when its vacancy was built by another `NORMALIZER_VERSION` (an older one,
or a newer one after a rollback). Pending items
are processed in batches, one transaction each, and written with
`INSERT … ON CONFLICT (raw_vacancy_id) DO UPDATE`, so a second run changes
nothing and two runs at once only repeat each other's work.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import Row, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vacancy_radar.db.models import RawVacancy, Skill, Source, Vacancy
from vacancy_radar.domain.money import to_monthly_usd
from vacancy_radar.domain.normalize import (
    NORMALIZER_VERSION,
    NORMALIZERS,
    NormalizedVacancy,
    kyiv_date,
    normalize,
)
from vacancy_radar.domain.skills import SKILLS
from vacancy_radar.exchange.rates import RateTable, ensure_rates

BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class NormalizeStats:
    """How many vacancies one run wrote, and how many of them have a salary."""

    normalized: int = 0
    with_salary: int = 0
    with_usd: int = 0

    def __add__(self, other: "NormalizeStats") -> "NormalizeStats":
        return NormalizeStats(
            self.normalized + other.normalized,
            self.with_salary + other.with_salary,
            self.with_usd + other.with_usd,
        )


def normalize_pending(
    session: Session,
    client: httpx.Client,
    *,
    rebuild: bool = False,
    batch_size: int = BATCH_SIZE,
    sleep: Callable[[float], None] = time.sleep,
) -> NormalizeStats:
    """Normalize every pending raw item, or every raw item with `rebuild`.

    Errors from NBU propagate. The batch that hit one is rolled back, and its
    items stay pending for the next run; earlier batches stay committed.
    """
    sync_skills(session)
    session.commit()

    stats = NormalizeStats()
    last_id = 0
    while batch := pending_batch(session, after_id=last_id, rebuild=rebuild, limit=batch_size):
        stats += normalize_batch(session, client, batch, sleep=sleep)
        session.commit()
        last_id = batch[-1].id
    return stats


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


def normalize_batch(
    session: Session,
    client: httpx.Client,
    batch: Sequence[Row[Any]],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> NormalizeStats:
    """Normalize one batch and upsert its vacancies. Does not commit."""
    normalized = [(row, normalize(row.slug, row.payload)) for row in batch]
    # Only salaries in other currencies need a rate, so most batches need none.
    days = {
        rate_day(vacancy, row.first_seen_at)
        for row, vacancy in normalized
        if vacancy.salary is not None and vacancy.salary.currency != "USD"
    }
    rates = ensure_rates(session, client, days, sleep=sleep) if days else {}

    values = [vacancy_values(row, vacancy, rates) for row, vacancy in normalized]
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
        with_usd=sum(
            v["salary_usd_min"] is not None or v["salary_usd_max"] is not None for v in values
        ),
    )


def rate_day(vacancy: NormalizedVacancy, first_seen_at: datetime) -> date:
    """The Kyiv day whose rate converts the salary: publication, else first sight."""
    return kyiv_date(vacancy.published_at or first_seen_at)


def vacancy_values(row: Row[Any], vacancy: NormalizedVacancy, rates: RateTable) -> dict[str, Any]:
    """Column values of the `vacancy` row for one raw item."""
    salary = vacancy.salary
    usd_min, usd_max = (
        to_monthly_usd(salary, rates.get(rate_day(vacancy, row.first_seen_at)))
        if salary is not None
        else (None, None)
    )
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
        "salary_usd_min": usd_min,
        "salary_usd_max": usd_max,
        "seniority": list(vacancy.seniority),
        "work_formats": list(vacancy.work_formats),
        "skills": list(vacancy.skills),
        "normalizer_version": NORMALIZER_VERSION,
        "raw_updated_at": row.updated_at,
    }
