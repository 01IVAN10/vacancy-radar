"""The short excerpt of a description that a search result shows. Pure: no I/O.

Results show a short excerpt of the description, never all of it
(docs/sources.md, "Collection rules (all sources)"): with words, a fragment Postgres's
`ts_headline` picks around them (`db/fts.py:headline`); without words, the
beginning (`excerpt`). `snippet_html` is the only place where vacancy text
becomes HTML, and it escapes everything but its own highlights.
"""

import re
from html import escape
from typing import Final

# The longest excerpt shown, in characters, when no words are highlighted.
EXCERPT_LENGTH: Final = 300

# `ts_headline` puts these around the words it highlights. They are control
# characters, which normalized vacancy text never contains, and
# `snippet_html` turns them into `<mark>` only after escaping the text.
HIGHLIGHT_START: Final = "\x02"
HIGHLIGHT_STOP: Final = "\x03"
_HIGHLIGHTED: Final = re.compile(
    f"{HIGHLIGHT_START}([^{HIGHLIGHT_START}{HIGHLIGHT_STOP}]*){HIGHLIGHT_STOP}"
)
_MARKERS: Final = re.compile(f"[{HIGHLIGHT_START}{HIGHLIGHT_STOP}]")


def snippet_html(text: str) -> str:
    """HTML for a piece of vacancy text, with `ts_headline`'s highlights as `<mark>`.

    Everything is escaped first, so the text cannot add markup of its own,
    and markers left without a pair are dropped.
    """
    marked = _HIGHLIGHTED.sub(r"<mark>\1</mark>", escape(text))
    return _MARKERS.sub("", marked)


def excerpt(text: str, limit: int = EXCERPT_LENGTH) -> str:
    """The beginning of `text`, cut at a word boundary within `limit` characters."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip(" ,.;:-–—") + "…"
