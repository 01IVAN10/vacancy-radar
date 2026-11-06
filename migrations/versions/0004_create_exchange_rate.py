"""create exchange_rate

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_rate",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("uah_per_unit", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.CheckConstraint("currency IN ('USD', 'EUR')", name=op.f("ck_exchange_rate_currency")),
        sa.CheckConstraint("uah_per_unit > 0", name=op.f("ck_exchange_rate_uah_per_unit_positive")),
        sa.PrimaryKeyConstraint("day", "currency", name=op.f("pk_exchange_rate")),
    )


def downgrade() -> None:
    op.drop_table("exchange_rate")
