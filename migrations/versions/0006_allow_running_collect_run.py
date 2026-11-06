"""record collect runs as running before the request

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Autogenerate does not compare CHECK constraints, so these are written by hand.
# `op.f()` marks the name as final, so the naming convention is not applied twice.
STATUS = "ck_collect_run_status"
FINISHED = "ck_collect_run_finished"


def upgrade() -> None:
    op.alter_column("collect_run", "finished_at", nullable=True)
    op.drop_constraint(op.f(STATUS), "collect_run", type_="check")
    op.create_check_constraint(
        op.f(STATUS), "collect_run", "status IN ('running', 'ok', 'failed', 'dry_run')"
    )
    op.create_check_constraint(
        op.f(FINISHED), "collect_run", "(status = 'running') = (finished_at IS NULL)"
    )


def downgrade() -> None:
    # An interrupted run was still a request, so it is kept as a failed one
    # instead of being deleted: it must keep counting against `min_interval`.
    op.execute(
        sa.text(
            "UPDATE collect_run SET status = 'failed', finished_at = started_at,"
            " error_type = 'Abandoned' WHERE status = 'running'"
        )
    )
    op.drop_constraint(op.f(FINISHED), "collect_run", type_="check")
    op.drop_constraint(op.f(STATUS), "collect_run", type_="check")
    op.create_check_constraint(op.f(STATUS), "collect_run", "status IN ('ok', 'failed', 'dry_run')")
    op.alter_column("collect_run", "finished_at", nullable=False)
