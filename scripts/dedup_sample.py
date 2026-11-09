"""Measure deduplication on a backup of the hosted database, and rebuild its test fixture.

    uv run python scripts/dedup_sample.py measure BACKUP.dump
    uv run python scripts/dedup_sample.py fixture BACKUP.dump tests/fixtures/dedup/pairs.json

BACKUP.dump is a `pg_dump --format=custom` backup (README, backing up the
hosted database). Its `source` and `raw_vacancy` rows are read into memory
with `pg_restore` (the `PG_RESTORE` variable, else `pg_restore` on PATH).
Every item is normalized with the current parsers and clustered in first-seen
order, as the cluster assignment does. Nothing is written to a database and
nothing is fetched from a source. `docs/deduplication.md` reports the output
of `measure`.

`fixture` writes the real pairs listed in `PAIRS`. Each vacancy keeps its feed
title, which public pages show anyway, and its description only as shingle
hashes made with a random key that is thrown away, so the text cannot be
rebuilt from them (see `domain.dedup.shingles`).
"""

import argparse
import base64
import json
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
from itertools import combinations
from pathlib import Path
from typing import Any

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
    fixture = commands.add_parser("fixture")
    fixture.add_argument("backup", type=Path)
    fixture.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    items = load(args.backup)
    if args.command == "measure":
        measure(items)
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


def copy_rows(backup: Path, table: str) -> list[list[str]]:
    """The rows of one table's `COPY` data in the backup, as text fields."""
    pg_restore = os.environ.get("PG_RESTORE") or shutil.which("pg_restore")
    if pg_restore is None:
        sys.exit("pg_restore not found: set PG_RESTORE or put it on PATH")
    # Runs a local binary on a local file the operator named; no shell.
    dump = subprocess.run(  # noqa: S603
        [pg_restore, "--data-only", "--table", table, "--file", "-", str(backup)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows: list[list[str]] = []
    inside = False
    for line in dump.splitlines():
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
            f"sources {dict(Counter(map(sources_of, multi)))})"
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

    places = Counter(city for item in items for city in item.vacancy.cities)
    flags = {
        city: n for city, n in places.items() if city in ("віддалено", "гібрид", "за кордоном")
    }
    print(f"distinct places: {len(places)}; format or abroad flags among them: {flags}")


def similar_pairs(facts: dict[str, DedupFacts], threshold: float) -> list[tuple[str, str]]:
    """Pairs whose texts share at least `threshold`, found through rare shingles."""
    holders: defaultdict[int, list[str]] = defaultdict(list)
    for key, fact in facts.items():
        for shingle in fact.shingles:
            holders[shingle].append(key)
    candidates = {
        pair
        for keys in holders.values()
        if len(keys) <= 8
        for pair in combinations(sorted(keys), 2)
    }
    return sorted(
        (a, b)
        for a, b in candidates
        if dedup.text_similarity(facts[a].shingles, facts[b].shingles) >= threshold
    )


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


def sources_of(group: frozenset[str]) -> str:
    return "+".join(sorted({key.split(":")[0] for key in group}))


def title_of(key: str, items: list[Item]) -> str:
    return next(item.vacancy.title for item in items if item.key == key)


def describe(group: frozenset[str], items: list[Item], facts: dict[str, DedupFacts]) -> str:
    least = min(
        dedup.text_similarity(facts[a].shingles, facts[b].shingles)
        for a, b in combinations(sorted(group), 2)
    )
    titles = ", ".join(f"{key} {title_of(key, items)!r}" for key in sorted(group))
    return f"text {least:.2f}: {titles}"


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
