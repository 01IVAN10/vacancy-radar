import pytest

from vacancy_radar.domain.text import html_to_text, normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # DOU titles arrive double-escaped (tests/fixtures/dou/rss_all.xml).
        pytest.param(
            "Інженер-пілот БпЛА (R&amp;amp;D)", "Інженер-пілот БпЛА (R&D)", id="double-escaped"
        ),
        pytest.param(
            "Lead of Sales &amp; Distribution", "Lead of Sales & Distribution", id="escaped"
        ),
        # A real DOU title in mathematical bold letters.
        pytest.param(
            "𝗦𝗲𝗻𝗶𝗼𝗿 𝗗𝗲𝘃𝗢𝗽𝘀 𝗘𝗻𝗴𝗶𝗻𝗲𝗲𝗿 (𝗔𝘇𝘂𝗿𝗲 / 𝗔𝗞𝗦)",
            "Senior DevOps Engineer (Azure / AKS)",
            id="bold",
        ),
        # Djinni separates thousands with no-break and thin spaces.
        pytest.param("40\u00a0000\u2009–\u202f60 000 грн", "40 000 – 60 000 грн", id="spaces"),
        pytest.param("  a \n\t b  ", "a b", id="collapse"),
        pytest.param("", "", id="empty"),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<ul><li>Python</li><li>SQL</li></ul>", "Python SQL", id="list-items-apart"),
        pytest.param(
            "<p>Зарплата:</p><p><strong>40&nbsp;000 грн</strong></p>",
            "Зарплата: 40 000 грн",
            id="paragraphs",
        ),
        pytest.param("line<br>next<br/>last", "line next last", id="br"),
        pytest.param("<b>Rust</b>ing", "Rusting", id="inline-tags-join"),
        pytest.param("&#36;500 &amp;amp; more", "$500 & more", id="entities"),
        pytest.param("plain text", "plain text", id="no-markup"),
    ],
)
def test_html_to_text(markup: str, expected: str) -> None:
    assert html_to_text(markup) == expected
