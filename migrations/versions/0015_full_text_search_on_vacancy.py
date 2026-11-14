"""full-text search on vacancies, and a GIN index on their skills

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-14 20:00:00.000000

Vacancy texts mix Ukrainian and English, and Postgres ships no Ukrainian
text search configuration, so search uses `simple_unaccent`: the `simple`
configuration (lower case, no stemming, no stop words) with accents removed
by `unaccent` first.

`search_text` joins Ukrainian words that the parser would split at their
apostrophe: "об'єкт", "об’єкт" and "обʼєкт" all become "обєкт". It is
applied to the stored texts and to every query, so the three spellings find
each other. Apostrophes outside Ukrainian words ("Google's") stay as they are.

`vacancy.search_vector` is a stored generated column, so it is always in step
with its row, normalization needs no change, and a rebuild from raw data
rebuilds it too. Title words weigh most, then the company, then the
description.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Token types that hold words; the others (URLs, emails, numbers with signs)
# keep the plain `simple` mapping.
WORD_TOKENS = (
    "asciiword, asciihword, hword_asciipart, word, hword, hword_part, "
    "numword, numhword, hword_numpart"
)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS unaccent"))
    op.execute(sa.text("CREATE TEXT SEARCH CONFIGURATION simple_unaccent (COPY = simple)"))
    op.execute(
        sa.text(
            f"ALTER TEXT SEARCH CONFIGURATION simple_unaccent "
            f"ALTER MAPPING FOR {WORD_TOKENS} WITH unaccent, simple"
        )
    )
    # Cyrillic letters are listed in both cases instead of using the `i` flag,
    # so the result never depends on the database's locale.
    op.execute(
        sa.text(
            "CREATE FUNCTION search_text(value text) RETURNS text "
            "LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE "
            "RETURN regexp_replace("
            "value, '([А-ЯІЇЄҐа-яіїєґ])[''’ʼ`](?=[А-ЯІЇЄҐа-яіїєґ])', '\\1', 'g')"
        )
    )
    op.add_column(
        "vacancy",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('simple_unaccent'::regconfig, search_text(title)), 'A') "
                "|| setweight(to_tsvector('simple_unaccent'::regconfig, "
                "search_text(coalesce(company, ''))), 'B') "
                "|| setweight(to_tsvector('simple_unaccent'::regconfig, "
                "search_text(description)), 'C')",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_vacancy_search_vector", "vacancy", ["search_vector"], postgresql_using="gin"
    )
    op.create_index("ix_vacancy_skills", "vacancy", ["skills"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_vacancy_skills", table_name="vacancy")
    op.drop_index("ix_vacancy_search_vector", table_name="vacancy")
    op.drop_column("vacancy", "search_vector")
    op.execute(sa.text("DROP FUNCTION search_text(text)"))
    op.execute(sa.text("DROP TEXT SEARCH CONFIGURATION simple_unaccent"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS unaccent"))
