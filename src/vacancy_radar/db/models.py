"""Tables for collected data.

`raw_vacancy` keeps every fetched item exactly as the source returned it, so
the normalized tables of later stages can be rebuilt from it at any time.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vacancy_radar.db.base import Base


class Source(Base):
    """A site vacancies are collected from, such as Djinni."""

    __tablename__ = "source"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RawVacancy(Base):
    """One item from a source feed, stored unchanged.

    `(source_id, external_id)` is unique, so collecting the same feed twice
    updates rows instead of duplicating them. `updated_at` moves only when the
    payload actually changes.
    """

    __tablename__ = "raw_vacancy"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="RESTRICT"))
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
