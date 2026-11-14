from vacancy_radar.domain.snippet import (
    EXCERPT_LENGTH,
    HIGHLIGHT_START,
    HIGHLIGHT_STOP,
    excerpt,
    snippet_html,
)


def mark(text: str) -> str:
    return f"{HIGHLIGHT_START}{text}{HIGHLIGHT_STOP}"


def test_snippet_marks_the_highlighted_words() -> None:
    assert snippet_html(f"Know {mark('Python')} and {mark('SQL')}") == (
        "Know <mark>Python</mark> and <mark>SQL</mark>"
    )


def test_snippet_escapes_the_text_and_its_highlights() -> None:
    text = f"<script>alert(1)</script> & {mark('<b>Go</b>')}"

    assert snippet_html(text) == (
        "&lt;script&gt;alert(1)&lt;/script&gt; &amp; <mark>&lt;b&gt;Go&lt;/b&gt;</mark>"
    )


def test_snippet_drops_markers_without_a_pair() -> None:
    assert snippet_html(f"a{HIGHLIGHT_START}b {mark('c')} d{HIGHLIGHT_STOP}") == (
        "ab <mark>c</mark> d"
    )


def test_excerpt_keeps_a_short_text_whole() -> None:
    assert excerpt("Short text.") == "Short text."


def test_excerpt_cuts_a_long_text_at_a_word() -> None:
    text = "word, " * 100

    cut = excerpt(text)

    assert len(cut) <= EXCERPT_LENGTH
    assert cut.endswith("word…")
    assert text.startswith(cut.removesuffix("…"))


def test_excerpt_cuts_one_endless_word() -> None:
    assert excerpt("x" * 50, limit=10) == "x" * 9 + "…"
