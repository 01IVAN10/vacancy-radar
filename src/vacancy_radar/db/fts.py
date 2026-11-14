"""Full-text search over vacancies: how texts and queries become lexemes.

The texts mix Ukrainian and English, and Postgres ships no Ukrainian
configuration, so both sides use `simple_unaccent` (migration 0015): the
`simple` configuration, which lower-cases words and keeps them whole, with
accents removed by `unaccent` first ("résumé" finds "resume").

Before parsing, both sides also go through the SQL function `search_text`,
which drops the apostrophe inside a Ukrainian word: "об'єкт", "об’єкт" and
"обʼєкт" all become "обєкт". The parser would otherwise split the first two
into "об" and "єкт".

What `simple` cannot do, by design (docs/search.md):

- no stemming: "розробник" does not find "розробника";
- symbols are not words: "C++" and "C#" are both "c", ".NET" is "net".
  The skill filter finds technologies exactly;
- a dotted name is one word: "Node.js" is found by "node.js", not by "node".
"""

from typing import Any, Final

from sqlalchemy import ColumnElement, cast, func
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY

SEARCH_CONFIG: Final = "simple_unaccent"


def text_query(text: str) -> ColumnElement[Any]:
    """The tsquery of what a person typed, in web search syntax.

    Words must all appear, `"quoted words"` must appear in that order, `or`
    allows either side, and `-word` excludes a word. Any input is valid: a
    query with no words in it matches nothing instead of failing.
    """
    return func.websearch_to_tsquery(
        cast(SEARCH_CONFIG, REGCONFIG), func.search_text(text), type_=TSQUERY
    )
