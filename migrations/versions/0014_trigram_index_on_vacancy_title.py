"""find duplicate candidates by title with a trigram index

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-09 22:00:00.000000

Deduplication compares a new vacancy only with vacancies whose titles share
enough trigrams (`title % :title`), and this GIN index answers that without
reading the whole table. Stage 9 search uses the same index for titles.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.create_index(
        "ix_vacancy_title_trgm",
        "vacancy",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_vacancy_title_trgm", table_name="vacancy")
    op.execute(sa.text("DROP EXTENSION IF EXISTS pg_trgm"))
