"""split words joined by a slash for search

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14 23:00:00.000000

The text search parser reads "Python/FastAPI", "Bash/Python" or "C/C++" as
one token, a file path, so a search for "python" missed them: 25 of the 691
vacancies with the Python skill in the 2026-11-14 sample, and 2808 of 4405
descriptions join words with a slash. `search_text` now replaces a slash
between two word characters with a space, in stored texts and in queries
alike, so "ci/cd" still finds "CI/CD". Other slashes stay: turning "</p>"
into "< p>" would make the parser read the rest of the text as one unclosed
tag and drop it. Word characters are listed rather than matched by class,
so the result does not depend on the database's locale.

The function feeds a stored generated column, which Postgres computes only
when a row is written, so every row is rewritten to apply it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOIN_APOSTROPHES = (
    "regexp_replace(value, '([А-ЯІЇЄҐа-яіїєґ])[''’ʼ`](?=[А-ЯІЇЄҐа-яіїєґ])', '\\1', 'g')"
)
# Letters, digits, and the symbols that end a name: "C++/C#", "C#/.NET".
WORD = "0-9A-Za-zА-ЯІЇЄҐа-яіїєґ+#"


def _define(body: str) -> None:
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION search_text(value text) RETURNS text "
            f"LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE RETURN {body}"
        )
    )
    # Recompute `vacancy.search_vector` with the new definition.
    op.execute(sa.text("UPDATE vacancy SET title = title"))


def upgrade() -> None:
    _define(f"regexp_replace({JOIN_APOSTROPHES}, '([{WORD}])/(?=[{WORD}.])', '\\1 ', 'g')")


def downgrade() -> None:
    _define(JOIN_APOSTROPHES)
