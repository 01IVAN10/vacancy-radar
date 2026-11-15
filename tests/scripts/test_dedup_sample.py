"""The pure parts of `scripts/dedup_sample.py audit`: which pairs, why apart, which label."""

import json
import random
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from scripts.dedup_sample import (
    AuditedPair,
    audited_pairs,
    classify,
    close,
    pair_outcome,
    plainly_same,
    read_labels,
)
from vacancy_radar.domain.dedup import DedupFacts

TEXT = frozenset(range(100))
# Shares 75 of its 100 shingles with TEXT: Jaccard 0.6, containment 0.75, too
# little by either measure, across sources too.
EDITED_TEXT = frozenset(range(25, 100)) | frozenset(range(1000, 1025))


def facts(**overrides: Any) -> DedupFacts:
    fields: dict[str, Any] = {
        "source": "djinni",
        "title": "Python Developer",
        "company": None,
        "cities": (),
        "seniority": (),
        "salary": None,
        "shingles": TEXT,
    }
    return DedupFacts.of(**(fields | overrides))


# --- pair_outcome -----------------------------------------------------------------


def test_a_joined_pair_is_together() -> None:
    assert pair_outcome(facts(), facts(source="dou"), together=True) == "together"


def test_the_same_job_in_two_clusters_is_kept_apart_by_linkage() -> None:
    assert pair_outcome(facts(), facts(source="dou"), together=False) == "linkage"


def test_a_pair_kept_apart_only_by_its_text() -> None:
    edited = facts(source="dou", shingles=EDITED_TEXT)
    assert pair_outcome(facts(), edited, together=False) == "text"


def test_a_veto_names_the_rule_that_keeps_the_pair_apart() -> None:
    senior = facts(source="dou", title="Senior Python Developer", seniority=["senior"])
    assert pair_outcome(facts(), senior, together=False) == "seniority"
    other_title = facts(source="dou", title="Go Developer")
    assert pair_outcome(facts(), other_title, together=False) == "title"


# --- plainly_same -----------------------------------------------------------------


def test_the_same_title_words_and_text_are_plainly_the_same_job() -> None:
    assert plainly_same(facts(), facts(source="dou", title="python developer"))


def test_a_title_with_more_words_or_an_edited_text_needs_a_hand_check() -> None:
    assert not plainly_same(facts(), facts(source="dou", title="Python Developer (Fintech)"))
    assert not plainly_same(facts(), facts(source="dou", shingles=EDITED_TEXT))


# --- classify ---------------------------------------------------------------------


def test_a_hand_label_wins_over_the_plain_rule() -> None:
    """A pair labelled different stays a wrong merge even if it looks plainly the same."""
    pair_facts = {"djinni:1": facts(), "dou:2": facts(source="dou")}
    clusters = {"djinni:1": 7, "dou:2": 7}

    [labelled] = classify(
        [("djinni:1", "dou:2")], pair_facts, clusters, {"djinni:1|dou:2": "different"}
    )
    [unlabelled] = classify([("djinni:1", "dou:2")], pair_facts, clusters, {})

    assert labelled == AuditedPair("djinni:1", "dou:2", "together", "different", plain=True)
    assert labelled.verdict == "different"
    assert unlabelled.verdict == "same"


def test_a_pair_that_is_not_plain_needs_a_label() -> None:
    pair_facts = {"djinni:1": facts(), "dou:2": facts(source="dou", title="Python Dev (Fintech)")}

    [pair] = classify([("djinni:1", "dou:2")], pair_facts, {"djinni:1": 1, "dou:2": 2}, {})

    assert pair.outcome == "title"
    assert pair.verdict is None


# --- audited_pairs ----------------------------------------------------------------


def test_finds_pairs_by_either_measure_across_sources_only() -> None:
    pairs = audited_pairs(
        {
            "djinni:1": facts(),
            "dou:1": facts(source="dou"),  # the same text
            "djinni:2": facts(),  # the same text, but the same source as djinni:1
            "dou:2": facts(source="dou", shingles=frozenset(range(30))),  # contained
            "dou:3": facts(source="dou", shingles=frozenset(range(60, 160))),  # 0.25 / 0.4
            "dou:4": facts(source="dou", shingles=frozenset(range(10))),  # too short
        }
    )

    assert pairs == [
        ("djinni:1", "dou:1"),
        ("djinni:1", "dou:2"),
        ("djinni:2", "dou:1"),
        ("djinni:2", "dou:2"),
    ]


def test_finds_pairs_whose_shared_shingles_are_all_common() -> None:
    """Boilerplate held by many vacancies still makes a pair: no rare shingle is needed."""
    vacancies = {f"djinni:{n}": facts() for n in range(20)} | {"dou:1": facts(source="dou")}

    assert audited_pairs(vacancies) == sorted((f"djinni:{n}", "dou:1") for n in range(20))


def test_finds_a_pair_that_shares_only_its_commonest_shingles() -> None:
    """The boundary of prefix filtering: the pair shares just enough, all of it common.

    Each text has 10 shingles of its own and 20 shared (Jaccard 20 / 40 =
    0.5). The shared ones are also in long texts of other vacancies, so they
    are the commonest, and only the last shingle of the prefix finds the pair.
    """
    shared = frozenset(range(20))
    vacancies = {
        "djinni:a": facts(shingles=shared | frozenset(range(100, 110))),
        "dou:b": facts(source="dou", shingles=shared | frozenset(range(200, 210))),
    } | {
        f"djinni:{n}": facts(shingles=shared | frozenset(range(1000 * n, 1000 * n + 200)))
        for n in range(1, 4)
    }

    assert audited_pairs(vacancies) == [("djinni:a", "dou:b")]


def test_finds_the_same_pairs_as_comparing_every_pair() -> None:
    """Prefix filtering loses nothing against a brute-force comparison."""
    rng = random.Random(0)  # noqa: S311 - repeatable test data, not a secret
    # A skewed vocabulary, so some shingles are common and some rare.
    vocabulary = [n for n in range(400) for _ in range(1 + n % 7)]
    base = [frozenset(rng.sample(vocabulary, 60)) for _ in range(8)]
    vacancies = {}
    for n in range(80):
        text = set(rng.choice(base)) if n % 2 else set(rng.sample(vocabulary, 60))
        text -= set(rng.sample(sorted(text), rng.randrange(25)))
        text |= set(rng.sample(vocabulary, rng.randrange(40)))
        vacancies[f"{'djinni' if n % 3 else 'dou'}:{n}"] = facts(
            source="djinni" if n % 3 else "dou", shingles=frozenset(text)
        )

    expected = sorted(
        (a, b)
        for a, b in combinations(sorted(vacancies), 2)
        if vacancies[a].source != vacancies[b].source and close(vacancies[a], vacancies[b])
    )
    assert len(expected) >= 10  # the data has close pairs to find
    assert audited_pairs(vacancies) == expected


# --- read_labels ------------------------------------------------------------------


def write_labels(path: Path, **labels: str) -> Path:
    pairs = {key.replace("__", "|"): {"label": label, "note": ""} for key, label in labels.items()}
    path.write_text(json.dumps({"pairs": pairs}))
    return path


def test_reads_labels_by_pair_key(tmp_path: Path) -> None:
    path = write_labels(tmp_path / "labels.json", a__b="same", c__d="unsure")
    assert read_labels(path) == {"a|b": "same", "c|d": "unsure"}


def test_an_unknown_label_stops_the_audit(tmp_path: Path) -> None:
    path = write_labels(tmp_path / "labels.json", a__b="maybe")
    with pytest.raises(SystemExit, match="maybe"):
        read_labels(path)
