"""group vacancies into clusters, one per vacancy until deduplication

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-08 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vacancy_cluster",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vacancy_cluster")),
    )
    op.create_index(op.f("ix_vacancy_cluster_first_seen_at"), "vacancy_cluster", ["first_seen_at"])
    op.add_column("vacancy", sa.Column("cluster_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_vacancy_cluster_id_vacancy_cluster"),
        "vacancy",
        "vacancy_cluster",
        ["cluster_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_vacancy_cluster_id"), "vacancy", ["cluster_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_vacancy_cluster_id"), table_name="vacancy")
    op.drop_constraint(op.f("fk_vacancy_cluster_id_vacancy_cluster"), "vacancy", type_="foreignkey")
    op.drop_column("vacancy", "cluster_id")
    op.drop_index(op.f("ix_vacancy_cluster_first_seen_at"), table_name="vacancy_cluster")
    op.drop_table("vacancy_cluster")
