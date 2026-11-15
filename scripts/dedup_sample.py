"""Measure deduplication on a backup of the hosted database, check it, and rebuild its fixture.

    uv run python scripts/dedup_sample.py measure BACKUP.dump
    uv run python scripts/dedup_sample.py audit BACKUP.dump [scripts/dedup_labels.json]
    uv run python scripts/dedup_sample.py check BACKUP.dump DATABASE_URL
    uv run python scripts/dedup_sample.py fixture BACKUP.dump tests/fixtures/dedup/pairs.json

BACKUP.dump is a `pg_dump --format=custom` backup (README, backing up the
hosted database). Its `source` and `raw_vacancy` rows are read into memory
with `pg_restore` (the `PG_RESTORE` variable, else `pg_restore` on PATH).
Every item is normalized with the current parsers and clustered in first-seen
order with `domain.dedup.pick_cluster`, in memory. `docs/deduplication.md`
reports the output of `measure`.

`audit` checks the rule across sources against hand labels. It takes every
pair of vacancies from different sources whose texts are close by either
measure (`AUDIT_JACCARD`, `AUDIT_CONTAINMENT`), whatever the rule says about
them, and reads each one's label (`same`, `different` or `unsure`) from
`dedup_labels.json`. A hand label always wins. A pair without one counts as
the same job only when the rule joins it with the same title words and texts
sharing at least `TEXT_THRESHOLD` ("plain"). It prints how many joined pairs
are wrong, how many of the same jobs are joined, and why the rest are kept
apart, and exits 1 while a pair needs a label.

`measure` compares every new vacancy with every cluster, so it does not go
through the cluster assignment's trigram search and time window. `check`
closes that gap: it migrates an empty local database whose name ends in
`_check`, loads the backup's sources and raw items into it, and runs the
real `normalize_pending` and `assign_clusters` on it, then compares their
clusters with the in-memory ones and exits 1 if they differ. It writes only
to that database, which the operator creates and drops. No command fetches anything from a source.

`fixture` writes the real pairs listed in `PAIRS`. Each vacancy keeps its feed
title, which public pages show anyway, and its description only as shingle
hashes made with a random key that is thrown away, so the text cannot be
rebuilt from them (see `domain.dedup.shingles`).
"""

import argparse
import base64
import json
import math
import os
import random
import re
import secrets
import shutil
import struct
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from vacancy_radar.clusters.service import MAX_PER_RUN, Clustered, assign_clusters
from vacancy_radar.db.models import RawVacancy, Source, Vacancy
from vacancy_radar.domain import dedup
from vacancy_radar.domain.dedup import (
    TEXT_THRESHOLD,
    DedupFacts,
    Same,
    pick_cluster,
    same_job,
    shingles,
)
from vacancy_radar.domain.normalize import NORMALIZER_VERSION, NormalizedVacancy, normalize
from vacancy_radar.normalize.service import normalize_pending
from vacancy_radar.notify.matching import LOOKBACK

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

# (a, b, expected verdict, why, known miss). A known miss expects "same" and is
# marked xfail: the rule keeps the pair apart today, and a rule that joins it
# would be an improvement, not a regression.
PAIRS: tuple[tuple[str, str, str, str, bool], ...] = (
    ("djinni:851784", "dou:375686", "same", "R&D on DOU, RnD on Djinni", False),
    ("dou:379096", "djinni:857122", "same", "'&' on DOU, 'and' on Djinni", False),
    ("djinni:844234", "dou:374137", "same", "DOU adds '(Deftech)' to the title", False),
    ("dou:379273", "djinni:857208", "same", "Djinni adds 'Strong' to the title", False),
    (
        "dou:379212",
        "djinni:857119",
        "same",
        "'Head of User Acquisition' vs 'Head of Acquisition'",
        False,
    ),
    (
        "djinni:856935",
        "dou:379083",
        "same",
        "same title; only DOU knows the company and places",
        False,
    ),
    ("djinni:856919", "djinni:850925", "same", "Djinni repost under an older id", False),
    ("djinni:857045", "djinni:857224", "same", "Djinni repost three days later", False),
    (
        "djinni:853540",
        "djinni:853534",
        "same",
        "one source, '/' and '\\' in otherwise equal titles",
        False,
    ),
    (
        "djinni:856981",
        "dou:379105",
        "same",
        "Tieto role whose introduction other Tieto roles share",
        False,
    ),
    ("dou:379219", "djinni:857134", "same", "'&' on DOU, ',' on Djinni", False),
    ("djinni:857260", "dou:379325", "same", "Ukrainian word in the title, '/' and '|'", False),
    ("djinni:857020", "dou:379146", "same", "Ukrainian title and text", False),
    (
        "djinni:850654",
        "djinni:850657",
        "title",
        "one text: Retention Specialist (Onboarding) vs Retention Manager",
        False,
    ),
    (
        "djinni:850654",
        "djinni:850655",
        "title",
        "one text: Retention Specialist (Onboarding) vs (Loyalty)",
        False,
    ),
    (
        "dou:379107",
        "dou:379105",
        "seniority",
        "Telco Engineer vs Junior Telco Engineer, one source",
        False,
    ),
    (
        "djinni:856979",
        "dou:379105",
        "seniority",
        "Telco vs Junior Telco across sources: titles nest, texts match",
        False,
    ),
    ("dou:365331", "dou:365330", "title", "iOS vs Android developer, one team text", False),
    (
        "djinni:857041",
        "djinni:857039",
        "title",
        "Account Executive for Romania vs for Bulgaria",
        False,
    ),
    ("djinni:829100", "djinni:831989", "seniority", "Middle vs Senior 3D Modeling Engineer", False),
    (
        "dou:379332",
        "dou:379333",
        "seniority",
        "Middle vs Senior .NET Engineer at one company",
        False,
    ),
    (
        "dou:379138",
        "dou:379254",
        "company",
        "one recruiter's text for two companies' MLOps roles",
        False,
    ),
    ("dou:351238", "dou:351241", "title", "two roles in one military unit, one text", False),
    (
        "djinni:857087",
        "djinni:857085",
        "text",
        "same title posted twice at once with different texts",
        False,
    ),
    (
        "djinni:854459",
        "dou:379134",
        "title",
        "Upwork Sales Manager vs IT Sales Manager, one company text",
        False,
    ),
    (
        "djinni:857010",
        "dou:379281",
        "title",
        "Customer Support vs L1 Technical Support, one company text",
        False,
    ),
    (
        "dou:379369",
        "dou:379371",
        "seniority",
        "Embedded Test Engineer (Senior / Middle) vs (Junior)",
        False,
    ),
    (
        "djinni:857279",
        "dou:379347",
        "text",
        "'Product Manager' at a company that hires several",
        False,
    ),
    (
        "dou:379313",
        "djinni:857273",
        "same",
        "probably one job, but the texts share only 0.75",
        True,
    ),
)
THRESHOLDS = (TEXT_THRESHOLD, 0.7, 0.5)
# Random arrival orders to compare with first-seen order.
SHUFFLES = 10
# `measure` finds pairs through shingles held by at most this many vacancies:
# a common one is boilerplate. `audit` does not use it: it must see every
# close pair, whatever shingles it shares.
RARE_SHINGLE = 8
# Pairs from different sources that `audit` labels by hand: texts that share
# this much by Jaccard, or this much of the shorter text.
AUDIT_JACCARD = 0.5
AUDIT_CONTAINMENT = 0.75
AUDIT_LABELS = Path(__file__).with_name("dedup_labels.json")

type Label = Literal["same", "different", "unsure"]
LABELS: tuple[Label, ...] = ("same", "different", "unsure")
# Joined in one cluster, or why the pair is apart: a rule of `same_job`, or
# "linkage" when the rule calls them the same job but one of them joined a
# cluster the other does not fit (complete linkage, see Arrival order in
# docs/deduplication.md).
type Outcome = Literal["together", "linkage"] | dedup.Reason
# The order `audit` prints outcomes in; docs/deduplication.md uses the same names.
OUTCOMES: tuple[Outcome, ...] = (
    "together",
    "text",
    "title",
    "seniority",
    "company",
    "cities",
    "salary",
    "linkage",
)


@dataclass(frozen=True, slots=True)
class Item:
    raw_id: int
    source: str
    external_id: str
    first_seen_at: str
    payload: dict[str, Any]
    vacancy: NormalizedVacancy

    @property
    def key(self) -> str:
        return f"{self.source}:{self.external_id}"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("measure").add_argument("backup", type=Path)
    audit_command = commands.add_parser("audit")
    audit_command.add_argument("backup", type=Path)
    audit_command.add_argument("labels", type=Path, nargs="?", default=AUDIT_LABELS)
    check_command = commands.add_parser("check")
    check_command.add_argument("backup", type=Path)
    check_command.add_argument("database_url", help="an empty database whose name ends in _check")
    fixture = commands.add_parser("fixture")
    fixture.add_argument("backup", type=Path)
    fixture.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    items = load(args.backup)
    if args.command == "measure":
        measure(items)
    elif args.command == "audit":
        sys.exit(audit(items, args.labels))
    elif args.command == "check":
        sys.exit(check(args.backup, items, make_url(args.database_url)))
    else:
        write_fixture(items, args.out)


# --- reading the backup -----------------------------------------------------------


def load(backup: Path) -> list[Item]:
    """Every raw item of the backup, normalized, in first-seen order."""
    sources = {row[0]: row[1] for row in copy_rows(backup, "source")}
    items = []
    # Columns of raw_vacancy: id, source_id, external_id, url, payload, first_seen_at, ...
    for raw_id, source_id, external_id, _, payload, first_seen_at, *_ in copy_rows(
        backup, "raw_vacancy"
    ):
        source = sources[source_id]
        parsed = json.loads(payload)
        items.append(
            Item(int(raw_id), source, external_id, first_seen_at, parsed, normalize(source, parsed))
        )
    return sorted(items, key=lambda item: (item.first_seen_at, item.raw_id))


def pg_restore() -> str:
    found = os.environ.get("PG_RESTORE") or shutil.which("pg_restore")
    if found is None:
        sys.exit("pg_restore not found: set PG_RESTORE or put it on PATH")
    return found


def restore_data(backup: Path, table: str, *target: str) -> list[str]:
    """`pg_restore` arguments that write one table's data from `backup` to `target`."""
    return [pg_restore(), "--data-only", "--table", table, *target, str(backup)]


def copy_rows(backup: Path, table: str) -> list[list[str]]:
    """The rows of one table's `COPY` data in the backup, as text fields."""
    # Runs a local binary on a local file the operator named; no shell.
    dump = subprocess.run(  # noqa: S603
        restore_data(backup, table, "--file", "-"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows: list[list[str]] = []
    inside = False
    # COPY ends rows with "\n" only; splitlines() would also split on U+2028 and
    # other separators that COPY leaves unescaped inside descriptions.
    for line in dump.split("\n"):
        if line.startswith("COPY "):
            inside = True
        elif inside and line == "\\.":
            inside = False
        elif inside:
            rows.append([unescape_copy(field) for field in line.split("\t")])
    return rows


COPY_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}


def unescape_copy(field: str) -> str:
    return re.sub(r"\\(.)", lambda m: COPY_ESCAPES.get(m[1], m[1]), field)


def facts_of(item: Item, *, key: bytes = b"") -> DedupFacts:
    vacancy = item.vacancy
    return DedupFacts.of(
        source=item.source,
        title=vacancy.title,
        company=vacancy.company,
        cities=vacancy.cities,
        seniority=vacancy.seniority,
        salary=vacancy.salary,
        shingles=shingles(vacancy.description, key=key),
    )


# --- measure ----------------------------------------------------------------------


def measure(items: list[Item]) -> None:
    facts = {item.key: facts_of(item) for item in items}
    print(f"items: {len(items)} {dict(Counter(item.source for item in items))}")
    print(f"first seen: {items[0].first_seen_at} .. {items[-1].first_seen_at}")

    close = similar_pairs(facts, TEXT_THRESHOLD)
    verdicts = Counter(verdict(same_job(facts[a], facts[b])) for a, b in close)
    print(f"pairs sharing >= {TEXT_THRESHOLD} of text: {len(close)} {dict(verdicts)}")

    previous: set[frozenset[str]] = set()
    for threshold in THRESHOLDS:
        groups = clusters(items, facts, threshold=threshold)
        multi = [group for group in groups if len(group) > 1]
        print(
            f"threshold {threshold}: {len(groups)} clusters, {len(multi)} with several "
            f"(sizes {dict(Counter(map(len, multi)))}, "
            f"sources {dict(Counter('+'.join(sorted(sources_of(g, facts))) for g in multi))})"
        )
        for group in multi:
            if previous and group not in previous:
                print(f"  added: {describe(group, items, facts)}")
            elif threshold == TEXT_THRESHOLD and len({title_of(key, items) for key in group}) > 1:
                print(f"  titles differ: {describe(group, items, facts)}")
        previous = set(multi)

    first_seen = set(clusters(items, facts))
    rng = random.Random(0)  # noqa: S311 - a repeatable order, not a secret
    changed = []
    for _ in range(SHUFFLES):
        shuffled = items.copy()
        rng.shuffle(shuffled)
        changed.append(len(first_seen ^ set(clusters(shuffled, facts))) // 2)
    print(f"clusters that differ in {SHUFFLES} random arrival orders: max {max(changed)}")

    late_members(items, first_seen)

    places = Counter(city for item in items for city in item.vacancy.cities)
    flags = {
        city: n for city, n in places.items() if city in ("віддалено", "гібрид", "за кордоном")
    }
    print(f"distinct places: {len(places)}; format or abroad flags among them: {flags}")


def late_members(items: list[Item], groups: Iterable[frozenset[str]]) -> None:
    """How long after its cluster's first posting each later member was first seen."""
    by_key = {item.key: item for item in items}
    late = []
    for group in groups:
        members = sorted((datetime.fromisoformat(by_key[key].first_seen_at), key) for key in group)
        first_seen, first = members[0]
        for seen, key in members[1:]:
            late.append((seen - first_seen, key, first))
    if not late:
        return
    beyond = [entry for entry in late if entry[0] > LOOKBACK]
    other_source = [entry for entry in beyond if entry[1].split(":")[0] != entry[2].split(":")[0]]
    other_format = [
        entry
        for entry in other_source
        if by_key[entry[1]].vacancy.work_formats != by_key[entry[2]].vacancy.work_formats
    ]
    longest = max(gap for gap, _, _ in late)
    print(
        f"later members: {len(late)}; first seen more than {LOOKBACK} after their cluster: "
        f"{len(beyond)} ({len(other_source)} from the other source, {len(other_format)} of "
        f"them with other work formats); longest gap {longest.total_seconds() / 3600:.1f} h"
    )


def sharing_pairs(facts: dict[str, DedupFacts]) -> set[tuple[str, str]]:
    """Pairs of vacancies that share at least one rare shingle, each sorted."""
    holders: defaultdict[int, list[str]] = defaultdict(list)
    for key, fact in facts.items():
        for shingle in fact.shingles:
            holders[shingle].append(key)
    return {
        pair
        for keys in holders.values()
        if len(keys) <= RARE_SHINGLE
        for pair in combinations(sorted(keys), 2)
    }


def similar_pairs(facts: dict[str, DedupFacts], threshold: float) -> list[tuple[str, str]]:
    """Pairs whose texts share at least `threshold`, found through rare shingles."""
    return sorted(
        (a, b)
        for a, b in sharing_pairs(facts)
        if dedup.text_similarity(facts[a].shingles, facts[b].shingles) >= threshold
    )


def containment(a: frozenset[int], b: frozenset[int]) -> float:
    """The share of the smaller shingle set found in the other; 0.0 if either is too short."""
    if len(a) < dedup.MIN_SHINGLES or len(b) < dedup.MIN_SHINGLES:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def clusters(
    items: Iterable[Item], facts: dict[str, DedupFacts], *, threshold: float = TEXT_THRESHOLD
) -> list[frozenset[str]]:
    """Cluster the items in the given order with `pick_cluster`."""
    members: dict[int, list[str]] = {}
    for number, item in enumerate(items):
        found = pick_cluster(
            facts[item.key],
            {cid: [facts[k] for k in keys] for cid, keys in members.items()},
            threshold=threshold,
        )
        members.setdefault(number if found is None else found, []).append(item.key)
    return [frozenset(keys) for keys in members.values()]


def verdict(comparison: dedup.Comparison) -> str:
    return "same" if isinstance(comparison, Same) else comparison.reason


def sources_of(group: Iterable[str], facts: dict[str, DedupFacts]) -> set[str]:
    return {facts[key].source for key in group}


def title_of(key: str, items: list[Item]) -> str:
    return next(item.vacancy.title for item in items if item.key == key)


def describe(group: frozenset[str], items: list[Item], facts: dict[str, DedupFacts]) -> str:
    least = min(
        dedup.text_similarity(facts[a].shingles, facts[b].shingles)
        for a, b in combinations(sorted(group), 2)
    )
    titles = ", ".join(f"{key} {title_of(key, items)!r}" for key in sorted(group))
    return f"text {least:.2f}: {titles}"


# --- audit ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditedPair:
    """A pair from different sources, what the rule did with it, and its hand label."""

    a: str
    b: str
    outcome: Outcome
    # None when the pair has no hand label.
    label: Label | None
    # Joined with the same title words and texts sharing `TEXT_THRESHOLD`: the
    # same job without a hand check, unless a label says otherwise.
    plain: bool

    @property
    def key(self) -> str:
        return f"{self.a}|{self.b}"

    @property
    def verdict(self) -> Label | None:
        """The hand label; else "same" for a plain pair; else None: it needs a label."""
        if self.label is not None:
            return self.label
        return "same" if self.plain else None


def audit(items: list[Item], labels_path: Path) -> int:
    """Compare the clusters across sources with hand labels; 1 while a pair needs a label."""
    labels = read_labels(labels_path)
    by_key = {item.key: item for item in items}
    facts = {item.key: facts_of(item) for item in items}
    groups = clusters(items, facts)
    cluster_of = {key: number for number, group in enumerate(groups) for key in group}
    pairs = classify(audited_pairs(facts), facts, cluster_of, labels)

    print(
        f"pairs from different sources with texts sharing >= {AUDIT_JACCARD} (Jaccard) or "
        f">= {AUDIT_CONTAINMENT} of the shorter one: {len(pairs)}"
    )
    for outcome in OUTCOMES:
        if of_outcome := [pair for pair in pairs if pair.outcome == outcome]:
            print(f"  {outcome}: {len(of_outcome)}; {count_labels(of_outcome)}")

    joined = [pair for pair in pairs if pair.outcome == "together"]
    checked = Counter(pair.label for pair in joined if pair.label is not None)
    print(
        f"wrong merges: {checked['different']} of {checked.total()} joined pairs labelled "
        f"by hand ({checked['unsure']} unsure); {len(joined) - checked.total()} joined "
        "plainly, without a label"
    )
    same = [pair for pair in pairs if pair.verdict == "same"]
    print(f"same jobs joined: {sum(pair.outcome == 'together' for pair in same)} of {len(same)}")
    for source in sorted({fact.source for fact in facts.values()}):
        with_source = [group for group in groups if source in sources_of(group, facts)]
        shared = sum(len(sources_of(group, facts)) > 1 for group in with_source)
        print(f"jobs on {source} also on another source: {shared} of {len(with_source)}")

    for pair in sorted(same, key=lambda pair: (OUTCOMES.index(pair.outcome), pair.key)):
        if pair.outcome != "together":
            print(f"  missed ({pair.outcome}): {describe_pair(pair, by_key, facts)}")
    for pair in pairs:
        if pair.plain and pair.label is not None:
            print(f"  labelled {pair.label}, joined plainly: {describe_pair(pair, by_key, facts)}")
    for key in sorted(labels.keys() - {pair.key for pair in pairs}):
        print(f"  label of a pair no longer audited (parsers changed?): {key}")
    unlabelled = [pair for pair in pairs if pair.verdict is None]
    for pair in unlabelled:
        print(f"  needs a label: {describe_pair(pair, by_key, facts)}")
        print(f"    {by_key[pair.a].payload.get('link')} {by_key[pair.b].payload.get('link')}")
    if unlabelled:
        print(f"{len(unlabelled)} pairs need a label in {labels_path}")
    return 1 if unlabelled else 0


def read_labels(path: Path) -> dict[str, Label]:
    """The hand labels by pair key; exits on a label outside `LABELS`."""
    labels: dict[str, Label] = {}
    for key, entry in json.loads(path.read_text())["pairs"].items():
        if entry["label"] not in LABELS:
            sys.exit(f"{path}: {key} is labelled {entry['label']!r}, expected one of {LABELS}")
        labels[key] = entry["label"]
    return labels


def audited_pairs(facts: dict[str, DedupFacts]) -> list[tuple[str, str]]:
    """Every pair from different sources whose texts are close by either measure, each sorted.

    Either measure needs the pair to share at least `floor` of the smaller
    shingle set, since a Jaccard similarity `j` means a containment of at
    least `2j / (1 + j)`. Two sets that share that many must share one of the
    smaller set's rarest `size - needed + 1` shingles (prefix filtering), so
    only those are looked up: no close pair is lost, however common the
    shingles it shares.
    """
    usable = {key: fact for key, fact in facts.items() if len(fact.shingles) >= dedup.MIN_SHINGLES}
    frequency = Counter(shingle for fact in usable.values() for shingle in fact.shingles)
    holders: defaultdict[int, list[str]] = defaultdict(list)
    for key, fact in usable.items():
        for shingle in fact.shingles:
            holders[shingle].append(key)
    floor = min(AUDIT_CONTAINMENT, 2 * AUDIT_JACCARD / (1 + AUDIT_JACCARD))

    candidates: set[tuple[str, str]] = set()
    for key, fact in usable.items():
        size = len(fact.shingles)
        rarest = sorted(fact.shingles, key=lambda shingle: (frequency[shingle], shingle))
        # Rounded down, so a float error can only make the prefix longer.
        for shingle in rarest[: size - math.floor(floor * size) + 1]:
            for other in holders[shingle]:
                if usable[other].source != fact.source and len(usable[other].shingles) >= size:
                    candidates.add((key, other) if key < other else (other, key))
    return sorted(pair for pair in candidates if close(facts[pair[0]], facts[pair[1]]))


def close(a: DedupFacts, b: DedupFacts) -> bool:
    """Texts that share `AUDIT_JACCARD` by Jaccard or `AUDIT_CONTAINMENT` of the shorter one."""
    return (
        dedup.text_similarity(a.shingles, b.shingles) >= AUDIT_JACCARD
        or containment(a.shingles, b.shingles) >= AUDIT_CONTAINMENT
    )


def classify(
    pairs: Iterable[tuple[str, str]],
    facts: dict[str, DedupFacts],
    cluster_of: dict[str, int],
    labels: dict[str, Label],
) -> list[AuditedPair]:
    """Each pair with its outcome under the clusters `cluster_of` and its hand label."""
    audited = []
    for a, b in pairs:
        outcome = pair_outcome(facts[a], facts[b], together=cluster_of[a] == cluster_of[b])
        plain = outcome == "together" and plainly_same(facts[a], facts[b])
        audited.append(AuditedPair(a, b, outcome, labels.get(f"{a}|{b}"), plain))
    return audited


def pair_outcome(a: DedupFacts, b: DedupFacts, *, together: bool) -> Outcome:
    """`together`, or why the pair is apart: a rule of `same_job`, or `linkage`."""
    if together:
        return "together"
    comparison = same_job(a, b, threshold=0.0)
    if isinstance(comparison, dedup.Different):
        return comparison.reason
    return "linkage" if isinstance(same_job(a, b), Same) else "text"


def plainly_same(a: DedupFacts, b: DedupFacts) -> bool:
    """The same title words and texts sharing `TEXT_THRESHOLD`: no hand check needed."""
    return (
        a.title_words == b.title_words
        and dedup.text_similarity(a.shingles, b.shingles) >= TEXT_THRESHOLD
    )


def count_labels(pairs: Sequence[AuditedPair]) -> str:
    """How many of `pairs` have each label, are plain without one, or need one."""
    labels = Counter(pair.label for pair in pairs if pair.label is not None)
    plain = sum(pair.label is None and pair.plain for pair in pairs)
    needed = sum(pair.verdict is None for pair in pairs)
    counts = [f"{label} {labels[label]}" for label in LABELS] + [f"plain, no label {plain}"]
    return ", ".join(counts + ([f"NEED A LABEL {needed}"] if needed else []))


def describe_pair(pair: AuditedPair, by_key: dict[str, Item], facts: dict[str, DedupFacts]) -> str:
    a, b = facts[pair.a], facts[pair.b]
    company = by_key[pair.a].vacancy.company or by_key[pair.b].vacancy.company
    return (
        f"{pair.key} Jaccard {dedup.text_similarity(a.shingles, b.shingles):.2f} "
        f"containment {containment(a.shingles, b.shingles):.2f} "
        f"{by_key[pair.a].vacancy.title!r} | {by_key[pair.b].vacancy.title!r} ({company})"
    )


# --- check ------------------------------------------------------------------------


def check(backup: Path, items: list[Item], url: URL) -> int:
    """Cluster a copy of the backup with the real code; 0 if it matches `measure`, else 1."""
    if not (url.database or "").endswith("_check"):
        sys.exit("the database name must end in _check: the backup is restored into it")
    url = url.set(drivername="postgresql+psycopg")
    engine = create_engine(url)
    with engine.connect() as connection:
        tables = connection.scalar(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        )
    if tables:
        sys.exit(f"{url.database} is not empty: create a new database for each check")

    config = Config(str(ALEMBIC_INI))
    # configparser reads "%" as interpolation, so a password with one needs it doubled.
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    command.upgrade(config, "head")
    # Only the raw items: the schema comes from this checkout's migrations, and
    # everything else is rebuilt from them. Neon's schema dump also carries
    # column options a plain Postgres rejects (`pg_attver`). A later migration
    # that adds a required column to these tables needs a default, or older
    # backups stop loading. One table at a time, so `source` is in before the
    # raw items that refer to it.
    for table in ("source", "raw_vacancy"):
        # Connection details as options and the password in the environment,
        # so the password does not show in the process list.
        subprocess.run(  # noqa: S603 - a local binary, a local file, no shell
            restore_data(
                backup,
                table,
                "--host",
                url.host or "localhost",
                "--port",
                str(url.port or 5432),
                "--username",
                url.username or "",
                "--dbname",
                url.database or "",
            ),
            check=True,
            env=os.environ | {"PGPASSWORD": url.password or ""},
        )

    with Session(engine) as session:
        print(f"normalized: {normalize_pending(session)}")
        while isinstance(run := assign_clusters(session), Clustered):
            if run.assigned < MAX_PER_RUN:
                break
        rows = session.execute(
            select(Vacancy.cluster_id, Source.slug, RawVacancy.external_id)
            .join(RawVacancy, RawVacancy.id == Vacancy.raw_vacancy_id)
            .join(Source, Source.id == Vacancy.source_id)
        ).all()
    groups: defaultdict[int | None, set[str]] = defaultdict(set)
    for cluster_id, slug, external_id in rows:
        groups[cluster_id].add(f"{slug}:{external_id}")
    produced = {frozenset(keys) for keys in groups.values()}

    facts = {item.key: facts_of(item) for item in items}
    expected = set(clusters(items, facts))
    print(
        f"assign_clusters: {len(produced)} clusters, "
        f"{sum(len(group) > 1 for group in produced)} with several; "
        f"in memory: {len(expected)}, {sum(len(group) > 1 for group in expected)}"
    )
    for group in sorted(produced ^ expected, key=sorted):
        side = "only assign_clusters" if group in produced else "only in memory"
        print(f"  {side}: {sorted(group)}")
    return 0 if produced == expected else 1


# --- fixture ----------------------------------------------------------------------


def write_fixture(items: list[Item], out: Path) -> None:
    by_key = {item.key: item for item in items}
    # Thrown away when the process ends: without it the hashes cannot be tried word by word.
    key = secrets.token_bytes(16)
    vacancies: dict[str, dict[str, Any]] = {}
    pairs: list[dict[str, Any]] = []
    for a, b, expected, why, known_miss in PAIRS:
        for item_key in (a, b):
            item = by_key[item_key]
            vacancies[item_key] = {
                "source": item.source,
                "feed_title": item.payload["title"],
                "shingles": pack(shingles(item.vacancy.description, key=key)),
            }
        full = verdict(same_job(facts_of(by_key[a], key=key), facts_of(by_key[b], key=key)))
        from_titles = verdict(same_job(title_facts(vacancies[a]), title_facts(vacancies[b])))
        wanted = "text" if known_miss else expected
        # The test sees only feed titles; a salary stated in a description is lost.
        if full != wanted or from_titles != wanted:
            sys.exit(f"{a} / {b}: expected {wanted}, got {full} (from titles {from_titles})")
        pair: dict[str, Any] = {"a": a, "b": b, "expected": expected, "why": why}
        if known_miss:
            pair["known_miss"] = True
        pairs.append(pair)
        print(f"{expected:9} {'(known miss) ' if known_miss else ''}{a} / {b}")
    fixture = {"normalizer_version": NORMALIZER_VERSION, "vacancies": vacancies, "pairs": pairs}
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n")
    print(f"{out}: {out.stat().st_size} bytes, {len(vacancies)} vacancies, {len(pairs)} pairs")


def title_facts(stored: dict[str, Any]) -> DedupFacts:
    """The facts the test builds: the feed title normalized, the stored shingles."""
    vacancy = normalize(stored["source"], {"title": stored["feed_title"]})
    return DedupFacts.of(
        source=stored["source"],
        title=vacancy.title,
        company=vacancy.company,
        cities=vacancy.cities,
        seniority=vacancy.seniority,
        salary=vacancy.salary,
        shingles=unpack(stored["shingles"]),
    )


def pack(hashes: frozenset[int]) -> str:
    """Sorted 32-bit hashes as base64 of big-endian integers."""
    return base64.b64encode(b"".join(struct.pack(">I", h) for h in sorted(hashes))).decode()


def unpack(packed: str) -> frozenset[int]:
    return frozenset(h for (h,) in struct.iter_unpack(">I", base64.b64decode(packed)))


if __name__ == "__main__":
    main()
