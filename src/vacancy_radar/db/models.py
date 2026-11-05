"""Tables for collected data.

`raw_vacancy` keeps every fetched item exactly as the source returned it, so
the normalized tables of later stages can be rebuilt from it at any time.
`collect_run` records every collection attempt, so gaps and missed items can
be found with SQL instead of reading job logs.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
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


class CollectRun(Base):
    """One attempt to collect one source, successful or not.

    A run with `status = 'ok'` carries the counts from `CollectStats`. A failed
    run carries the error instead. A `dry_run` fetched the source without
    storing items, so it has only `fetched` and `skipped`. Every status counts
    as a request to the source for its `min_interval`. `unchanged + updated` is
    the overlap with earlier runs: an `ok` run where it is 0 means the feed
    moved past items between two runs.
    """

    __tablename__ = "collect_run"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'failed', 'dry_run')", name="status"),
        Index("ix_collect_run_source_id_started_at", "source_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="RESTRICT"))
    # Both come from the collector's clock, so `finished_at - started_at` is the
    # run's duration. A server-side `now()` would be the transaction start time
    # on another machine's clock.
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16))
    fetched: Mapped[int | None]
    skipped: Mapped[int | None]
    inserted: Mapped[int | None]
    updated: Mapped[int | None]
    unchanged: Mapped[int | None]
    error_type: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
