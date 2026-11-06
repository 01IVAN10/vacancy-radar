"""Work formats a vacancy explicitly offers: remote, hybrid, office. Pure: no I/O.

Only explicit statements count. The words alone are not enough: descriptions
mention "hybrid cloud", "home office", "Microsoft Office", "back office", an
office with a generator, and office jobs grant "10 днів віддаленої роботи на
рік". A city in a DOU title does not mean office work either. A vacancy may
offer several formats ("повністю віддалено або в офісі"); all are kept.
"""

import re
from collections.abc import Iterable
from typing import Final, Literal

WorkFormat = Literal["remote", "hybrid", "office"]
WORK_FORMATS: Final[tuple[WorkFormat, ...]] = ("remote", "hybrid", "office")

# A label before a format word: "Location: Remote", "Формат роботи: гібрид",
# "Flexible working format - remote".
LABEL = (
    r"(?:location|format|work\ format|working\ format|arrangement|формат(?:\ роботи)?)\ ?[:—–-]\ ?"
)

# In titles, short words are explicit enough: "Data Engineer (Remote)",
# "Senior Quant Developer, Warsaw, hybrid". Not a bare "office": "Office Manager".
TITLE_WORDS: Final[dict[WorkFormat, str]] = {
    "remote": r"remote|віддалено|дистанційно",
    "hybrid": r"hybrid|гібрид\w*",
    "office": r"office-based|in-office|on-?site|в\ офісі",
}

TEXT_PHRASES: Final[dict[WorkFormat, str]] = {
    "remote": rf"""
        (?:fully|full|100%)\ remote|remote[-\ ]first|
        remote\ (?:work|working|position|role|job|format|setup|model|environment)|
        remote\ (?:option|cooperation)|
        work(?:ing)?\ (?:fully\ )?remotely|
        {LABEL}(?:fully\ )?remote|
        (?<!днів\ )(?:віддален|дистанційн)\w*\ (?:робот|формат|режим)\w*|
        # Not "з періодичною можливістю працювати дистанційно": an office job.
        (?<!періодичною\ можливістю\ )
        (?:працювати|працюємо|робота|роботу)\ (?:повністю\ )?(?:віддалено|дистанційно)|
        повністю\ (?:віддален|дистанційн)\w*|
        {LABEL}(?:віддален|дистанційн)\w*
    """,
    "hybrid": rf"""
        hybrid\ (?:work|working|format|model|mode|schedule|setup|role|position|option|arrangement)|
        гібридн\w*\ (?:формат|графік|робот|режим)\w*|
        {LABEL}(?:hybrid|гібрид)\w*|
        гібрид\ (?:у|в)\ [А-ЯІЇЄA-Z]\w*
    """,
    "office": rf"""
        office-based|in-office|on-?site\ (?:work|role|position|format)|
        work(?:ing)?\ (?:from|in|at)\ (?:the|our)\ office|
        (?:працю\w*|робот\w*)\ (?:в|у|з|із)\ офіс\w*|
        офісн\w*\ (?:формат|графік|робот|режим)\w*|
        {LABEL}(?:office|офіс)\w*
    """,
}


# "fully remote is not possible": a phrase followed by its own negation.
NEGATION = re.compile(
    r"\ ?(?:\(|—|-|:)?\ ?(?:is\ |are\ )?(?:not\ (?:possible|available|an\ option|offered)|"
    r"неможлив\w*|не\ (?:можлив|передбач|розгляда)\w*)",
    re.IGNORECASE | re.VERBOSE,
)


def _compile(words: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE | re.VERBOSE)


TITLE_PATTERNS = {fmt: _compile(words) for fmt, words in TITLE_WORDS.items()}
TEXT_PATTERNS = {fmt: _compile(phrases) for fmt, phrases in TEXT_PHRASES.items()}


def detect_work_formats(
    title: str, text: str, *, flagged: Iterable[WorkFormat] = ()
) -> tuple[WorkFormat, ...]:
    """Every format the title or the description states explicitly.

    `flagged` holds formats a source states in a structured field, such as
    `віддалено` in a DOU title. Expects text from `domain.text.normalize_text`.
    Returns formats in `WORK_FORMATS` order.
    """
    found = set(flagged) | {
        fmt
        for fmt in WORK_FORMATS
        if _states(TITLE_PATTERNS[fmt], title) or _states(TEXT_PATTERNS[fmt], text)
    }
    return tuple(fmt for fmt in WORK_FORMATS if fmt in found)


def _states(pattern: re.Pattern[str], text: str) -> bool:
    """Whether the text has a match of `pattern` that is not negated right after."""
    return any(not NEGATION.match(text, match.end()) for match in pattern.finditer(text))
