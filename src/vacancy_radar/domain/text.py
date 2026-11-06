"""Turning feed strings into plain, comparable text. Pure: no I/O.

Feeds send HTML descriptions, HTML entities that are sometimes escaped twice
(`&amp;amp;`), titles in Unicode "bold" letters, and numbers with no-break
and thin spaces as thousands separators. Every parser in `domain/` works on
the output of these functions, so it only has to handle plain text with
single ASCII spaces.
"""

import html
import re
import unicodedata
from html.parser import HTMLParser

# Tags whose boundaries separate words. Without a space there, `<li>Python</li><li>SQL</li>`
# would become "PythonSQL".
BLOCK_TAGS = frozenset(
    {"br", "p", "div", "li", "ul", "ol", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}
)

WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Unescape entities, apply NFKC and collapse whitespace into single spaces.

    NFKC turns look-alike characters into their plain form: mathematical bold
    letters into ASCII letters, and no-break and thin spaces into spaces.
    Entities are unescaped until nothing changes, which undoes double escaping.
    """
    previous = None
    while text != previous:
        previous, text = text, html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    return WHITESPACE.sub(" ", text).strip()


def html_to_text(markup: str) -> str:
    """Plain text of an HTML fragment, normalized with `normalize_text`.

    Only the text is kept. Block tags become spaces, so words from adjacent
    elements stay apart.
    """
    parser = _TextCollector()
    parser.feed(markup)
    parser.close()
    return normalize_text("".join(parser.parts))


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        # Entities are left in place and unescaped later by `normalize_text`,
        # together with any second level of escaping.
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")
