"""create vacancy and skill

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vacancy",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("raw_vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("cities", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(length=3), nullable=True),
        sa.Column("salary_period", sa.String(length=8), nullable=True),
        sa.Column("salary_usd_min", sa.Integer(), nullable=True),
        sa.Column("salary_usd_max", sa.Integer(), nullable=True),
        sa.Column("seniority", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("work_formats", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("skills", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("normalizer_version", sa.SmallInteger(), nullable=False),
        sa.Column("raw_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "normalized_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "salary_currency IN ('USD', 'EUR', 'UAH')", name=op.f("ck_vacancy_salary_currency")
        ),
        sa.CheckConstraint(
            "salary_period IN ('month', 'year', 'hour')", name=op.f("ck_vacancy_salary_period")
        ),
        sa.CheckConstraint("salary_min <= salary_max", name=op.f("ck_vacancy_salary_range")),
        sa.CheckConstraint(
            "(salary_currency IS NULL) = (salary_period IS NULL) "
            "AND (salary_currency IS NULL) = (salary_min IS NULL AND salary_max IS NULL)",
            name=op.f("ck_vacancy_salary_complete"),
        ),
        sa.CheckConstraint(
            "seniority <@ ARRAY['intern', 'junior', 'middle', 'senior', 'lead']::text[]",
            name=op.f("ck_vacancy_seniority_known"),
        ),
        sa.CheckConstraint(
            "work_formats <@ ARRAY['remote', 'hybrid', 'office']::text[]",
            name=op.f("ck_vacancy_work_formats_known"),
        ),
        sa.ForeignKeyConstraint(
            ["raw_vacancy_id"],
            ["raw_vacancy.id"],
            name=op.f("fk_vacancy_raw_vacancy_id_raw_vacancy"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["source.id"],
            name=op.f("fk_vacancy_source_id_source"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vacancy")),
        sa.UniqueConstraint("raw_vacancy_id", name=op.f("uq_vacancy_raw_vacancy_id")),
    )
    op.create_table(
        "skill",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_skill")),
    )


def downgrade() -> None:
    op.drop_table("skill")
    op.drop_table("vacancy")
