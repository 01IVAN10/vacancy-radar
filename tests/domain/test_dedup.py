"""Deduplication rules: each rule on made-up facts, then real pairs from collected data."""

import base64
import json
import struct
from typing import Any

import pytest

from tests.support import fixture_bytes
from vacancy_radar.domain.dedup import (
    MIN_SHINGLES,
    TEXT_THRESHOLD,
    DedupFacts,
    Different,
    Same,
    pick_cluster,
    same_job,
    shingles,
    text_similarity,
    title_words,
)
from vacancy_radar.domain.normalize import NORMALIZER_VERSION, normalize
from vacancy_radar.domain.salary import Salary

# Shingle sets built from integers: what matters is how much two sets share.
TEXT = frozenset(range(100))
# Shares 90 of 110 shingles with TEXT: 0.82, below the threshold.
OTHER_TEXT = frozenset(range(10, 110))


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


# --- titles -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("Product Designer (R&D)", "product designer (RnD)", id="r-and-d"),
        pytest.param("R & D Engineer", "RnD Engineer", id="r-and-d-spaced"),
        pytest.param(
            "Email Marketing & Retention", "Email Marketing and Retention", id="ampersand"
        ),
        pytest.param("Head of Farm / Farm Team Lead", "Head of Farm \\ Farm Team Lead", id="slash"),
        pytest.param("Аналітик та тестувальник", "Аналітик і тестувальник", id="ukrainian-and"),
    ],
)
def test_title_words_ignore_decoration(first: str, second: str) -> None:
    assert title_words(first) == title_words(second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Symbols are part of a language's name, as in `domain/skills.py`.
        pytest.param("Senior C# Developer", "Senior C++ Developer", id="csharp-cpp"),
        pytest.param("Embedded C Engineer", "Embedded C++ Engineer", id="c-cpp"),
        pytest.param("F# Developer", "F Developer", id="fsharp"),
        # Numbers name a role: a support line, a tier, a traffic tier.
        pytest.param("Support Engineer (Level 1)", "Support Engineer (Level 2)", id="level"),
        pytest.param("Support Specialist Tier 1", "Support Specialist Tier 2", id="tier"),
        pytest.param("Оператор 1 лінії підтримки", "Оператор 2 лінії підтримки", id="line"),
        pytest.param("Media Buyer T1", "Media Buyer T3", id="traffic-tier"),
    ],
)
def test_title_words_keep_what_tells_roles_apart(first: str, second: str) -> None:
    assert title_words(first) != title_words(second)


# --- shingles -----------------------------------------------------------------------


def test_shingles_ignore_case_and_punctuation() -> None:
    assert shingles("Python, SQL and AWS! Docker") == shingles("python SQL and aws docker")
    assert len(shingles("one two three four five six")) == 3


def test_text_shorter_than_one_shingle_has_none() -> None:
    assert shingles("only three words") == frozenset()


def test_a_key_changes_the_hashes_but_not_the_similarity() -> None:
    first = " ".join(f"word{n}" for n in range(60))
    second = " ".join(f"word{n}" for n in range(5, 65))
    key = b"a secret thrown away after use"

    assert shingles(first, key=key).isdisjoint(shingles(first))
    assert text_similarity(shingles(first, key=key), shingles(second, key=key)) == (
        text_similarity(shingles(first), shingles(second))
    )


def test_text_similarity_is_the_share_of_shared_shingles() -> None:
    assert text_similarity(TEXT, TEXT) == 1.0
    assert text_similarity(frozenset(range(30)), frozenset(range(10, 40))) == 0.5
    assert text_similarity(TEXT, frozenset(range(100, 200))) == 0.0


def test_short_texts_are_never_similar() -> None:
    short = frozenset(range(MIN_SHINGLES - 1))
    assert text_similarity(short, short) == 0.0


# --- same_job -----------------------------------------------------------------------


def test_same_title_and_text_in_one_source_is_the_same_job() -> None:
    assert same_job(facts(), facts()) == Same(1.0)


def test_one_source_needs_the_same_title_words() -> None:
    longer = facts(title="Python Developer (Fintech)")
    assert same_job(facts(), longer) == Different("title")


def test_across_sources_one_title_may_add_words() -> None:
    assert same_job(facts(), facts(source="dou", title="Python Developer (Fintech)")) == Same(1.0)


def test_across_sources_titles_that_both_add_words_differ() -> None:
    upwork = facts(title="Upwork Sales Manager")
    it = facts(source="dou", title="IT Sales Manager")
    assert same_job(upwork, it) == Different("title")


def test_across_sources_one_title_may_add_an_internal_number() -> None:
    plain = facts(title="Senior Web Analyst")
    assert same_job(plain, facts(source="dou", title="Senior Web Analyst (#1264)")) == Same(1.0)
    embedded = facts(title="Embedded Software Engineer")
    numbered = facts(source="dou", title="Embedded Software Engineer; ID 103860")
    assert same_job(embedded, numbered) == Same(1.0)


def test_one_source_with_two_internal_numbers_is_two_jobs() -> None:
    first = facts(source="dou", title="Senior Web Analyst (#1264)")
    second = facts(source="dou", title="Senior Web Analyst (#1301)")
    assert same_job(first, second) == Different("title")


def test_across_sources_cpp_is_not_csharp() -> None:
    cpp = facts(title="C++ Developer")
    csharp = facts(source="dou", title="C# / .NET Developer")
    assert same_job(cpp, csharp) == Different("title")


def test_a_title_without_words_matches_nothing() -> None:
    assert same_job(facts(title="(&)"), facts(source="dou")) == Different("title")


def test_text_below_the_threshold_is_another_job() -> None:
    assert same_job(facts(), facts(shingles=OTHER_TEXT)) == Different("text")


def test_measurements_may_try_another_threshold() -> None:
    lower = same_job(facts(), facts(shingles=OTHER_TEXT), threshold=0.8)
    assert lower == Same(text_similarity(TEXT, OTHER_TEXT))
    assert pick_cluster(facts(), {1: [facts(shingles=OTHER_TEXT)]}, threshold=0.8) == 1
    assert TEXT_THRESHOLD == 0.85


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param((), (), Same(1.0), id="both-unknown"),
        pytest.param(("middle",), ("middle",), Same(1.0), id="equal"),
        pytest.param((), ("junior",), Different("seniority"), id="one-unknown"),
        pytest.param(("middle", "senior"), ("senior",), Different("seniority"), id="overlapping"),
    ],
)
def test_levels_must_be_equal(
    first: tuple[str, ...], second: tuple[str, ...], expected: Same | Different
) -> None:
    assert same_job(facts(seniority=first), facts(seniority=second)) == expected


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param("SoftServe", " softserve ", Same(1.0), id="case-and-spaces"),
        pytest.param("SoftServe", None, Same(1.0), id="one-unknown"),
        pytest.param("SoftServe", "  ", Same(1.0), id="blank-is-unknown"),
        pytest.param("Talan AI", "ORB DRONES", Different("company"), id="different"),
    ],
)
def test_known_companies_must_agree(
    first: str | None, second: str | None, expected: Same | Different
) -> None:
    assert same_job(facts(company=first), facts(company=second)) == expected


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param(("Київ", "Львів"), ("львів",), Same(1.0), id="shared-place"),
        pytest.param(("Київ",), (), Same(1.0), id="one-unknown"),
        pytest.param(("Київ",), ("Одеса",), Different("cities"), id="disjoint"),
    ],
)
def test_known_places_must_overlap(
    first: tuple[str, ...], second: tuple[str, ...], expected: Same | Different
) -> None:
    assert same_job(facts(cities=first), facts(cities=second)) == expected


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        pytest.param(Salary(2000, 3000, "USD"), Salary(2500, 4000, "USD"), Same(1.0), id="overlap"),
        pytest.param(Salary(2000, 3000, "USD"), None, Same(1.0), id="one-unknown"),
        pytest.param(
            Salary(2000, 3000, "USD"), Salary(3500, 4000, "USD"), Different("salary"), id="apart"
        ),
        pytest.param(
            Salary(3000, None, "USD"), Salary(None, 2500, "USD"), Different("salary"), id="open"
        ),
        pytest.param(
            Salary(3000, None, "USD"), Salary(None, 3500, "USD"), Same(1.0), id="open-meet"
        ),
        # Different currencies or periods are not compared: rates and hours are unknown here.
        pytest.param(
            Salary(2000, 3000, "USD"), Salary(90_000, None, "UAH"), Same(1.0), id="currency"
        ),
        pytest.param(
            Salary(20, 30, "USD", "hour"), Salary(3000, 4000, "USD"), Same(1.0), id="period"
        ),
    ],
)
def test_salaries_in_one_currency_must_overlap(
    first: Salary | None, second: Salary | None, expected: Same | Different
) -> None:
    assert same_job(facts(salary=first), facts(salary=second)) == expected


def test_facts_store_a_blank_company_as_unknown() -> None:
    assert facts(company="  ").company is None
    assert facts(company=" Soft  Serve ").company == "soft serve"


def test_facts_refuse_an_unknown_level() -> None:
    with pytest.raises(ValueError, match="'principal'"):
        facts(seniority=("principal",))


# --- pick_cluster -------------------------------------------------------------------


def test_no_cluster_qualifies_without_clusters() -> None:
    assert pick_cluster(facts(), {}) is None


def test_joins_the_cluster_of_the_same_job() -> None:
    clusters = {1: [facts(title="Go Developer")], 2: [facts(source="dou")]}
    assert pick_cluster(facts(), clusters) == 2


def test_must_be_the_same_job_as_every_member() -> None:
    """A~B and B~C do not make A~C: C stays out of A's cluster."""
    django = facts(source="dou", title="Python Developer (Django)")
    flask = facts(source="dou", title="Python Developer (Flask)")
    plain = facts(title="Python Developer")
    assert same_job(django, plain) == Same(1.0)
    assert same_job(plain, flask) == Same(1.0)

    assert pick_cluster(flask, {1: [django, plain]}) is None


def test_prefers_the_cluster_whose_least_similar_member_is_closest() -> None:
    near = frozenset(range(2, 100))  # 0.98 with TEXT
    nearer = frozenset(range(1, 100))  # 0.99 with TEXT
    clusters = {1: [facts(shingles=near)], 2: [facts(shingles=TEXT), facts(shingles=nearer)]}
    assert pick_cluster(facts(), clusters) == 2


def test_of_equal_clusters_joins_the_oldest() -> None:
    assert pick_cluster(facts(), {7: [facts()], 3: [facts()]}) == 3


def test_skips_a_cluster_without_members() -> None:
    assert pick_cluster(facts(), {1: []}) is None


# --- real pairs ---------------------------------------------------------------------

# Pairs from the Neon backup of 2026-11-09, chosen for being hard: one job under
# decorated titles, different roles that share a text, and a known miss. Each
# vacancy keeps its feed title, normalized here by the real parsers, and its
# description as shingle hashes made with a key that was thrown away. See
# tests/fixtures/README.md; `scripts/dedup_sample.py fixture` rebuilds it.
PAIRS = json.loads(fixture_bytes("dedup/pairs.json"))


def stored_facts(key: str) -> DedupFacts:
    stored = PAIRS["vacancies"][key]
    # The feed title is all the fixture keeps of the payload, so a salary stated
    # only in the description is missing here; no pair depends on one.
    vacancy = normalize(stored["source"], {"title": stored["feed_title"]})
    packed = base64.b64decode(stored["shingles"])
    return DedupFacts.of(
        source=stored["source"],
        title=vacancy.title,
        company=vacancy.company,
        cities=vacancy.cities,
        seniority=vacancy.seniority,
        salary=vacancy.salary,
        shingles=frozenset(h for (h,) in struct.iter_unpack(">I", packed)),
    )


def real_pair(pair: dict[str, Any]) -> Any:
    marks = (
        [pytest.mark.xfail(strict=True, reason=f"known miss: {pair['why']}")]
        if pair.get("known_miss")
        else []
    )
    return pytest.param(pair, marks=marks, id=f"{pair['expected']}:{pair['a']}/{pair['b']}")


def test_fixture_matches_the_normalizer_version() -> None:
    """Its shingles come from descriptions as this version of the parsers reads them.

    After a new NORMALIZER_VERSION, rebuild it from a backup:
    `uv run python scripts/dedup_sample.py fixture BACKUP.dump tests/fixtures/dedup/pairs.json`.
    """
    assert PAIRS["normalizer_version"] == NORMALIZER_VERSION


@pytest.mark.parametrize("pair", [real_pair(pair) for pair in PAIRS["pairs"]])
def test_real_pairs(pair: dict[str, Any]) -> None:
    comparison = same_job(stored_facts(pair["a"]), stored_facts(pair["b"]))

    verdict = "same" if isinstance(comparison, Same) else comparison.reason
    assert verdict == pair["expected"], pair["why"]


def test_real_pairs_cover_every_outcome_seen_in_the_data() -> None:
    assert {pair["expected"] for pair in PAIRS["pairs"]} == {
        "same",
        "title",
        "seniority",
        "company",
        "text",
    }
