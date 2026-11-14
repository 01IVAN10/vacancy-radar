"""split words joined by a slash for search

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-14 23:00:00.000000

The text search parser reads "Python/FastAPI", "Bash/Python" or "C/C++" as
one token, a file path, so a search for "python" missed them: 25 of the 691
vacancies with the Python skill in the 2026-11-14 sample, and 2808 of 4405
descriptions join words with a slash.

`split_slashes` puts spaces around a slash between two word characters:
"Python / FastAPI". The parser then reads two words next to each other, and
the text still reads as written, so search excerpts are made from it too.
Other slashes stay: turning "</p>" into "< p>" would make the parser read
the rest of the text as one unclosed tag and drop it. Word characters are
listed rather than matched by class, so the result does not depend on the
database's locale. `search_text` now applies it after joining apostrophes.

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

FUNCTION = "LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE RETURN"
JOIN_APOSTROPHES = (
    "regexp_replace(value, '([А-ЯІЇЄҐа-яіїєґ])[''’ʼ`](?=[А-ЯІЇЄҐа-яіїєґ])', '\\1', 'g')"
)
# Letters, digits, and the symbols that end a name: "C++/C#", "C#/.NET".
WORD = "0-9A-Za-zА-ЯІЇЄҐа-яіїєґ+#"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"CREATE FUNCTION split_slashes(value text) RETURNS text {FUNCTION} "
            f"regexp_replace(value, '([{WORD}])/(?=[{WORD}.])', '\\1 / ', 'g')"
        )
    )
    _define_search_text(f"split_slashes({JOIN_APOSTROPHES})")


def downgrade() -> None:
    _define_search_text(JOIN_APOSTROPHES)
    op.execute(sa.text("DROP FUNCTION split_slashes(text)"))


def _define_search_text(body: str) -> None:
    op.execute(
        sa.text(
            f"CREATE OR REPLACE FUNCTION search_text(value text) RETURNS text {FUNCTION} {body}"
        )
    )
    # Recompute `vacancy.search_vector` with the new definition.
    op.execute(sa.text("UPDATE vacancy SET title = title"))
