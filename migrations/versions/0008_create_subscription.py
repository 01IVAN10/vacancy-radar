"""create subscription

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("skills", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("skills_match", sa.String(length=3), nullable=False),
        sa.Column("seniority", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("work_formats", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("min_salary_usd", sa.Integer(), nullable=True),
        sa.Column(
            "include_unsalaried", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "seniority <@ ARRAY['intern', 'junior', 'middle', 'senior', 'lead']::text[]",
            name=op.f("ck_subscription_seniority_known"),
        ),
        sa.CheckConstraint(
            "skills_match IN ('any', 'all')", name=op.f("ck_subscription_skills_match")
        ),
        sa.CheckConstraint(
            "work_formats <@ ARRAY['remote', 'hybrid', 'office']::text[]",
            name=op.f("ck_subscription_work_formats_known"),
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 80", name=op.f("ck_subscription_name_length")
        ),
        sa.CheckConstraint(
            "min_salary_usd > 0", name=op.f("ck_subscription_min_salary_usd_positive")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_subscription_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription")),
    )
    op.create_index(op.f("ix_subscription_user_id"), "subscription", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_subscription_user_id"), table_name="subscription")
    op.drop_table("subscription")
