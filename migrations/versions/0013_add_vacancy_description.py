"""store the plain description of every vacancy

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-09 21:00:00.000000

Existing rows get an empty description, which deduplication treats as "too
short to compare". `NORMALIZER_VERSION` 3 rebuilds them with the text. The
default is dropped afterwards, so every new row must carry a description.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vacancy",
        sa.Column("description", sa.Text(), server_default="", nullable=False),
    )
    op.alter_column("vacancy", "description", server_default=None)


def downgrade() -> None:
    op.drop_column("vacancy", "description")
