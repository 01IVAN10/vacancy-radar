"""Seniority levels named in a vacancy title. Pure: no I/O.

Only the title is read. Descriptions mention other levels too ("you will
mentor junior engineers", "report to the Head of Engineering"), and inferring
a level from years of experience is left for later (see PLAN.md, deferred).
A title may name several levels ("Strong Junior / Middle", "Senior / Lead"):
all of them are kept, so the vacancy matches a subscription to any of them.
"""

import re
from typing import Final, Literal

Level = Literal["intern", "junior", "middle", "senior", "lead"]
# From least to most senior. Results are returned in this order.
LEVELS: Final[tuple[Level, ...]] = ("intern", "junior", "middle", "senior", "lead")

# Whole words only, in English and Ukrainian.
LEVEL_WORDS: Final[dict[Level, str]] = {
    "intern": r"intern|interns|internship|trainee|стажер\w*|стажист\w*|практикант\w*",
    "junior": r"junior|jr\.?|джуніор|джун|молодш(?:ий|а)",
    # "mid" alone usually means middle ("Junior to Mid"), but not in "mid-market".
    # "Regular" is the Polish and EU name for it ("Regular/Senior Web Developer").
    "middle": r"middle|мідл|regular|mid(?:-|\ )level|mid(?!-(?:market|size))",
    "senior": r"senior|sr\.?|сеньйор|синьйор|сеніор|старш(?:ий|а)|провідн(?:ий|а)",
    # "Lead Generation Specialist" is a sales role, not a team lead.
    "lead": r"""
        (?:team|tech)\ ?lead|teamlead|techlead|техлід|
        lead(?!\ gen)|head|principal|staff|chief|
        cto|cio|cpo|cmo|ceo|coo|cfo|cro|vp|vice\ president|
        director|директор\w*|керівни(?:к|ця)\w*
    """,
}

PATTERNS: Final[dict[Level, re.Pattern[str]]] = {
    level: re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE | re.VERBOSE)
    for level, words in LEVEL_WORDS.items()
}


def detect_seniority(title: str) -> tuple[Level, ...]:
    """Every level the title names, from least to most senior.

    Expects text from `domain.text.normalize_text`. `"Middle+ / Senior Mobile
    iOS Developer"` gives `("middle", "senior")`; a title without a level
    gives `()`.
    """
    return tuple(level for level in LEVELS if PATTERNS[level].search(title))
