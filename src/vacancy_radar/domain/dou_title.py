"""The structured fields packed into a DOU feed title. Pure: no I/O.

DOU titles follow `<position> в <company>[, <salary>][, <places>][, <flags>]`:

    Інженер-пілот БпЛА (R&D) в Orients, $900–2200, Львів
    Senior Data Engineer в Innovation Group, Одеса, віддалено
    Marketing Analyst в Genesis, Київ, гібрид

The position may itself contain " в " ("Спеціаліст з кадрового обліку в
MilTech-компанію (NDA) в NDA (Miltech), Київ"), so the title is split at the
last one. A company name may contain ", " too ("Codebridge Technology, Inc.",
"Азов, 1 корпус НГУ"): segments with a legal suffix, or with a digit that is
not a salary (no place name has one), stay with the company.
"""

import re
from dataclasses import dataclass

from vacancy_radar.domain.salary import Salary, parse_salary_phrase
from vacancy_radar.domain.work_format import WORK_FORMATS, WorkFormat

SEPARATOR = " в "
ABROAD = "за кордоном"
FORMAT_FLAGS: dict[str, WorkFormat] = {"віддалено": "remote", "гібрид": "hybrid"}
# "Poland (remote)": a place that is open to remote work from there.
REMOTE_PLACE = re.compile(r"(?P<place>.+) \(remote\)", re.IGNORECASE)
COMPANY_PART = re.compile(
    r"\d|^(?:inc|llc|ltd|llp|plc|corp|gmbh|ag|s\.a|b\.v|sp\. z o\.o|тов|пп)\.?$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class DouTitle:
    position: str
    company: str | None
    salary: Salary | None = None
    # Places as DOU writes them: "Київ", "Варшава (Польща)", "Україна".
    cities: tuple[str, ...] = ()
    abroad: bool = False
    # Formats DOU flags in the title, in `WORK_FORMATS` order.
    work_formats: tuple[WorkFormat, ...] = ()


def parse_dou_title(title: str) -> DouTitle:
    """Split a normalized DOU title into its parts.

    A title without " в " is taken as a bare position. Expects text from
    `domain.text.normalize_text`.
    """
    position, separator, rest = title.rpartition(SEPARATOR)
    if not separator:
        return DouTitle(position=title.strip(), company=None)

    company, *segments = (segment.strip() for segment in rest.split(","))
    while segments and _is_company_part(segments[0]):
        company = f"{company}, {segments.pop(0)}"

    salary: Salary | None = None
    cities: list[str] = []
    formats: set[WorkFormat] = set()
    abroad = False
    for segment in segments:
        if segment == ABROAD:
            abroad = True
        elif segment in FORMAT_FLAGS:
            formats.add(FORMAT_FLAGS[segment])
        elif salary is None and (parsed := parse_salary_phrase(segment)) is not None:
            salary = parsed
        elif match := REMOTE_PLACE.fullmatch(segment):
            cities.append(match["place"])
            formats.add("remote")
        elif segment:
            cities.append(segment)

    return DouTitle(
        position=position.strip(),
        company=company or None,
        salary=salary,
        cities=tuple(cities),
        abroad=abroad,
        work_formats=tuple(fmt for fmt in WORK_FORMATS if fmt in formats),
    )


def _is_company_part(segment: str) -> bool:
    """A segment after the company that still belongs to its name."""
    return bool(COMPANY_PART.search(segment)) and parse_salary_phrase(segment) is None
