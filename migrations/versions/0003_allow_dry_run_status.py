"""allow dry_run status in collect_run

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06 00:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Autogenerate does not compare CHECK constraints, so this one is written by hand.
# `op.f()` marks the name as final, so the naming convention is not applied twice.
CONSTRAINT = "ck_collect_run_status"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "collect_run", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "collect_run", "status IN ('ok', 'failed', 'dry_run')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM collect_run WHERE status = 'dry_run'")
    op.drop_constraint(op.f(CONSTRAINT), "collect_run", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "collect_run", "status IN ('ok', 'failed')")
