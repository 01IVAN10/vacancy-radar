"""create collect_run

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06 00:00:31.951044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collect_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("fetched", sa.Integer(), nullable=True),
        sa.Column("skipped", sa.Integer(), nullable=True),
        sa.Column("inserted", sa.Integer(), nullable=True),
        sa.Column("updated", sa.Integer(), nullable=True),
        sa.Column("unchanged", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('ok', 'failed')", name=op.f("ck_collect_run_status")),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_collect_run_source_id_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collect_run")),
    )
    op.create_index(
        "ix_collect_run_source_id_started_at",
        "collect_run",
        ["source_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_collect_run_source_id_started_at", table_name="collect_run")
    op.drop_table("collect_run")
