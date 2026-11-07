"""check that the monthly dollar range keeps its order

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-08 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Autogenerate does not compare CHECK constraints, so this one is written by hand.
# `op.f()` marks the name as final, so the naming convention is not applied twice.
SALARY_USD_RANGE = "ck_vacancy_salary_usd_range"


def upgrade() -> None:
    op.create_check_constraint(
        op.f(SALARY_USD_RANGE), "vacancy", "salary_usd_min <= salary_usd_max"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(SALARY_USD_RANGE), "vacancy", type_="check")
