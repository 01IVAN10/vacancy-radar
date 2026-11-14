"""Full-text search over vacancies: how texts and queries become lexemes.

The texts mix Ukrainian and English, and Postgres ships no Ukrainian
configuration, so both sides use `simple_unaccent` (migration 0015): the
`simple` configuration, which lower-cases words and keeps them whole, with
accents removed by `unaccent` first ("résumé" finds "resume").

Before parsing, both sides also go through the SQL function `search_text`
(migrations 0015 and 0016), which fixes two ways the parser cuts words:

- it drops the apostrophe inside a Ukrainian word: "об'єкт", "об’єкт" and
  "обʼєкт" all become "обєкт", where the parser would split the first two
  into "об" and "єкт";
- it turns a slash between two words into a space: the parser reads
  "Python/FastAPI" as one file path, so "python" would not find it.

What `simple` cannot do, by design (docs/search.md):

- no stemming: "розробник" does not find "розробника";
- symbols are not words: "C++" and "C#" are both "c", ".NET" is "net".
  The skill filter finds technologies exactly;
- a dotted name is one word: "Node.js" is found by "node.js", not by "node".
"""

from typing import Any, Final

from sqlalchemy import ColumnElement, ColumnExpressionArgument, Float, Text, cast, func
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY

from vacancy_radar.domain.search import HIGHLIGHT_START, HIGHLIGHT_STOP

SEARCH_CONFIG: Final = "simple_unaccent"
# One fragment of at most 30 words around the best match: a short excerpt,
# not the description (CLAUDE.md, Legal data collection).
HEADLINE_OPTIONS: Final = (
    f"StartSel={HIGHLIGHT_START}, StopSel={HIGHLIGHT_STOP}, MaxWords=30, MinWords=12, ShortWord=2"
)


def text_query(text: str) -> ColumnElement[Any]:
    """The tsquery of what a person typed, in web search syntax.

    Words must all appear, `"quoted words"` must appear in that order, `or`
    allows either side, and `-word` excludes a word. Any input is valid: a
    query with no words in it matches nothing instead of failing.
    """
    return func.websearch_to_tsquery(
        cast(SEARCH_CONFIG, REGCONFIG), func.search_text(text), type_=TSQUERY
    )


def rank(
    vector: ColumnExpressionArgument[Any], query: ColumnExpressionArgument[Any]
) -> ColumnElement[float]:
    """How well a vector matches: more matches and heavier parts (title first) rank higher."""
    return func.ts_rank(vector, query, type_=Float)


def headline(
    text: ColumnExpressionArgument[Any], query: ColumnExpressionArgument[Any]
) -> ColumnElement[str]:
    """A short fragment of `text` around the query's words, which are wrapped in markers.

    `domain.search.snippet_html` turns the markers into `<mark>`. A word with
    an apostrophe in the text ("об'єкт") is not highlighted, because the text
    is shown as written while the query has no apostrophes. Without a match
    in `text`, the fragment is its beginning.
    """
    return func.ts_headline(
        cast(SEARCH_CONFIG, REGCONFIG), text, query, HEADLINE_OPTIONS, type_=Text
    )
