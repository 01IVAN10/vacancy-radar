"""The sites vacancies are collected from, as the `source` table lists them.

The one reader of that table for pages: search offers its sources as a
filter, analytics credits them, and both see the same set in the same order.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vacancy_radar.db.models import Source


@dataclass(frozen=True, slots=True)
class SourceSite:
    name: str
    url: str


async def source_sites(session: AsyncSession) -> dict[str, SourceSite]:
    """Every source by its slug, in slug order."""
    rows = await session.execute(
        select(Source.slug, Source.name, Source.base_url).order_by(Source.slug)
    )
    return {row.slug: SourceSite(row.name, row.base_url) for row in rows}
