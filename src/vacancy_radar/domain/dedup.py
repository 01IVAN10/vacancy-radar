"""Whether two vacancies are the same job, and which cluster a new one joins. Pure: no I/O.

Two vacancies are the same job when nothing they state contradicts it and
both their titles and their descriptions agree:

- **Vetoes.** Different levels, two known companies that differ, two known
  sets of places with none in common, or two salaries in the same currency
  and period whose ranges do not overlap.
- **Titles.** Within one source the title words must be the same: a company
  that posts two vacancies with different titles on one site means two
  roles. Across sources one title's words must be a subset of the other's,
  because sites decorate titles differently ("Embedded Software Engineer"
  on Djinni, "Embedded Software Engineer (R&D)" on DOU).
- **Descriptions.** At least `TEXT_THRESHOLD` of their word shingles are
  shared. Companies reuse an introduction across different roles, so text
  alone is never enough, and titles alone even less so.

A new vacancy joins a cluster only if it is the same job as every vacancy
already in it, so a chain of near matches never pulls two different jobs
together. The rule errs on the side of missing a duplicate: a wrong merge
hides a vacancy from subscribers, a missed one only sends it twice.
`docs/deduplication.md` has the measurements behind each threshold.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import blake2b
from typing import Final, Literal, Self

from vacancy_radar.domain.choices import one_of
from vacancy_radar.domain.salary import Salary
from vacancy_radar.domain.seniority import LEVELS, Level

# Share of shingles two descriptions of one job have in common at least. On the
# 2026-11-09 sample every merge at this level was right; lower levels started
# to merge different roles of one company (docs/deduplication.md).
TEXT_THRESHOLD: Final = 0.85
# Words per shingle: long enough that a common phrase ("experience with
# Python") is rarely what two texts share, short enough that one edited word
# changes only four shingles.
SHINGLE_SIZE: Final = 4
# A description with fewer shingles (about as many words) says too little to
# compare, so it never makes two vacancies the same job.
MIN_SHINGLES: Final = 20
# Title words that tell no two jobs apart: "and" in English and Ukrainian ("&"
# reads as "and", so "Sales & Marketing" and "Sales, Marketing" agree) and "в"
# ("analyst в AI-сферу"). Numbers stay: "Level 1" and "Level 2" support are two
# roles, and an internal number ("#1264", "ID 103860") added on one site only
# is covered by the subset rule across sources.
NOISE_WORDS: Final = frozenset({"and", "та", "і", "й", "в"})
# Names whose symbols are part of the word, spelled as plain words before the
# title is split. "C#" and "C++" are not "C" (as in `domain/skills.py`), and
# "R&D", "R & D" and "RnD" are one word.
SYMBOL_WORDS: Final = (
    (re.compile(r"(?<!\w)c\+\+"), " cpp "),
    (re.compile(r"(?<!\w)c#"), " csharp "),
    (re.compile(r"(?<!\w)f#"), " fsharp "),
    (re.compile(r"(?<!\w)r\s*&\s*d(?!\w)"), " rnd "),
)

WORD = re.compile(r"\w+")

Reason = Literal["seniority", "company", "cities", "salary", "title", "text"]


@dataclass(frozen=True, slots=True)
class DedupFacts:
    """What deduplication reads from one normalized vacancy.

    Build it with `of`, which puts each field in the form the rules compare.
    """

    source: str
    title_words: frozenset[str]
    # Casefolded, so "SoftServe" and "Softserve" are one company.
    company: str | None
    cities: frozenset[str]
    seniority: frozenset[Level]
    salary: Salary | None
    # Hashes of the description's shingles; see `shingles`.
    shingles: frozenset[int]

    @classmethod
    def of(
        cls,
        *,
        source: str,
        title: str,
        company: str | None,
        cities: Iterable[str],
        seniority: Iterable[str],
        salary: Salary | None,
        shingles: frozenset[int],
    ) -> Self:
        """Build the facts from a vacancy's fields and its description's `shingles`.

        Raises `ValueError` for a level outside `LEVELS`.
        """
        return cls(
            source=source,
            title_words=title_words(title),
            company=_casefold(company or "") or None,
            cities=frozenset(_casefold(city) for city in cities),
            seniority=frozenset(one_of(level, LEVELS) for level in seniority),
            salary=salary,
            shingles=shingles,
        )


@dataclass(frozen=True, slots=True)
class Same:
    """The same job; `similarity` is the share of shingles the descriptions share."""

    similarity: float


@dataclass(frozen=True, slots=True)
class Different:
    """Different jobs, and the first rule that told them apart."""

    reason: Reason


type Comparison = Same | Different


def title_words(title: str) -> frozenset[str]:
    """The meaningful words of a title, casefolded.

    `SYMBOL_WORDS` become plain words ("C#" is `csharp`), "&" reads as "and",
    and `NOISE_WORDS` are dropped, so "Email Marketing & Retention" and "Email
    Marketing and Retention" have the same words.
    """
    text = title.casefold()
    for pattern, word in SYMBOL_WORDS:
        text = pattern.sub(word, text)
    text = text.replace("&", " and ")
    return frozenset(word for word in WORD.findall(text) if word not in NOISE_WORDS)


def shingles(description: str, *, key: bytes = b"") -> frozenset[int]:
    """Hashes of every run of `SHINGLE_SIZE` consecutive words, casefolded.

    Each shingle is a 32-bit BLAKE2b hash, cheaper to compare than text. Two
    descriptions of about 500 shingles each share a hash by accident with a
    chance of about 1 in 17 000, which cannot move a similarity of 0.85.

    The hashes do not hide the text: neighbouring shingles share three words,
    so a description can be rebuilt from its hashes by trying one word at a
    time. Hashes that are published must use a secret `key` (up to 64 bytes)
    that is then thrown away; the similarity of two sets hashed with the same
    key is the same as without one.
    """
    words = WORD.findall(description.casefold())
    return frozenset(
        _hash(" ".join(words[start : start + SHINGLE_SIZE]), key)
        for start in range(len(words) - SHINGLE_SIZE + 1)
    )


def text_similarity(a: frozenset[int], b: frozenset[int]) -> float:
    """The Jaccard similarity of two shingle sets; 0.0 if either is too short to compare."""
    if len(a) < MIN_SHINGLES or len(b) < MIN_SHINGLES:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared)


def same_job(a: DedupFacts, b: DedupFacts, *, threshold: float = TEXT_THRESHOLD) -> Comparison:
    """Whether `a` and `b` are the same job; the cheap checks run first.

    `threshold` is the text similarity required; only measurements change it.
    """
    if a.seniority != b.seniority:
        return Different("seniority")
    if a.company is not None and b.company is not None and a.company != b.company:
        return Different("company")
    if a.cities and b.cities and not a.cities & b.cities:
        return Different("cities")
    if not _salaries_agree(a.salary, b.salary):
        return Different("salary")
    if not _titles_agree(a, b):
        return Different("title")
    similarity = text_similarity(a.shingles, b.shingles)
    if similarity < threshold:
        return Different("text")
    return Same(similarity)


def pick_cluster(
    new: DedupFacts,
    clusters: Mapping[int, Sequence[DedupFacts]],
    *,
    threshold: float = TEXT_THRESHOLD,
) -> int | None:
    """The cluster `new` joins, or None if it opens a cluster of its own.

    `new` qualifies for a cluster when it is the same job as every member.
    Of several, it joins the one whose least similar member is most similar,
    and of equals the one with the lowest id, so the choice is repeatable.
    """
    best: tuple[float, int] | None = None
    for cluster_id in sorted(clusters):
        members = clusters[cluster_id]
        if not members:
            continue
        comparisons = [same_job(new, member, threshold=threshold) for member in members]
        similarities = [c.similarity for c in comparisons if isinstance(c, Same)]
        if len(similarities) < len(comparisons):
            continue
        score = min(similarities)
        if best is None or score > best[0]:
            best = (score, cluster_id)
    return None if best is None else best[1]


def _titles_agree(a: DedupFacts, b: DedupFacts) -> bool:
    if not a.title_words or not b.title_words:
        return False
    if a.source == b.source:
        return a.title_words == b.title_words
    return a.title_words <= b.title_words or b.title_words <= a.title_words


def _salaries_agree(a: Salary | None, b: Salary | None) -> bool:
    """False only when both are stated in the same currency and period and do not overlap."""
    if a is None or b is None or (a.currency, a.period) != (b.currency, b.period):
        return True
    return _at_most(a.min_amount, b.max_amount) and _at_most(b.min_amount, a.max_amount)


def _at_most(low: int | None, high: int | None) -> bool:
    """`low <= high`, where a missing bound is open ("від 30 000" has no top)."""
    return low is None or high is None or low <= high


def _casefold(text: str) -> str:
    return " ".join(text.casefold().split())


def _hash(shingle: str, key: bytes) -> int:
    return int.from_bytes(blake2b(shingle.encode(), digest_size=4, key=key).digest(), "big")
