"""Normalization of whole feed items, end to end on the saved fixtures."""

import hashlib
from datetime import UTC, date, datetime, timedelta, timezone
from importlib.resources import files

import pytest

from tests.support import fixture_bytes
from vacancy_radar.domain.normalize import (
    NORMALIZER_VERSION,
    NORMALIZERS,
    kyiv_date,
    normalize,
    parse_pub_date,
)
from vacancy_radar.domain.salary import Salary
from vacancy_radar.sources import djinni, dou

DJINNI_ALL = {
    i.external_id: i.payload for i in djinni.parse_feed(fixture_bytes("djinni/rss_all.xml")).items
}
DJINNI_PYTHON = {
    i.external_id: i.payload
    for i in djinni.parse_feed(fixture_bytes("djinni/rss_python.xml")).items
}
DOU_ALL = {i.external_id: i.payload for i in dou.parse_feed(fixture_bytes("dou/rss_all.xml")).items}


def test_normalizes_a_djinni_item() -> None:
    vacancy = normalize("djinni", DJINNI_PYTHON["852433"])

    assert vacancy.title == "Senior Python / GCP Data Engineer"
    assert vacancy.company is None
    assert vacancy.cities == ()
    assert vacancy.category == "Python"
    assert vacancy.published_at == datetime(
        2026, 10, 8, 20, 31, 12, tzinfo=timezone(timedelta(hours=3))
    )
    assert vacancy.salary == Salary(2000, 3000, "USD")
    assert vacancy.seniority == ("senior",)
    assert vacancy.work_formats == ("remote",)
    assert {"python", "gcp", "bigquery", "airflow"} <= set(vacancy.skills)
    # Plain text: tags gone, block boundaries kept as spaces.
    assert vacancy.description.startswith(
        "Position: Senior Python / GCP Data Engineer Location: Remote Employment Type: Full-Time"
    )


def test_normalizes_a_dou_item_from_its_title() -> None:
    vacancy = normalize("dou", DOU_ALL["379053"])

    assert vacancy.title == "Senior DevOps Engineer (Azure / AKS)"  # bold letters, normalized
    assert vacancy.company == "NextDev IT Teams"
    assert vacancy.category is None
    assert vacancy.salary == Salary(4500, 5000, "USD")
    assert vacancy.seniority == ("senior",)
    assert vacancy.work_formats == ("remote",)
    assert {"azure", "kubernetes", "terraform"} <= set(vacancy.skills)
    assert vacancy.description.startswith("Senior DevOps Engineer (Azure / AKS) Remote, for")


def test_dou_flags_join_formats_found_in_the_text() -> None:
    vacancy = normalize("dou", DOU_ALL["379075"])  # SoftServe: "віддалено" + "гібридному форматі"

    assert vacancy.company == "SoftServe"
    assert len(vacancy.cities) == 12
    assert vacancy.work_formats == ("remote", "hybrid")


@pytest.mark.parametrize(
    ("source", "payloads", "with_salary"),
    [
        pytest.param("djinni", DJINNI_ALL, {"852239", "850004"}, id="djinni-all"),
        pytest.param("djinni", DJINNI_PYTHON, {"856641", "856036", "852433"}, id="djinni-python"),
        pytest.param("dou", DOU_ALL, {"379090", "379053"}, id="dou-all"),
    ],
)
def test_every_fixture_item_normalizes(
    source: str, payloads: dict[str, dict[str, object]], with_salary: set[str]
) -> None:
    vacancies = {ext_id: normalize(source, payload) for ext_id, payload in payloads.items()}

    assert all(v.title and v.published_at is not None for v in vacancies.values())
    assert {ext_id for ext_id, v in vacancies.items() if v.salary is not None} == with_salary


def test_tolerates_missing_and_malformed_fields() -> None:
    vacancy = normalize("djinni", {"title": None, "categories": [""], "pubDate": "yesterday"})

    assert vacancy.title == ""
    assert vacancy.category is None
    assert vacancy.published_at is None
    assert vacancy.salary is None
    assert vacancy.description == ""


def test_unknown_source_has_no_mapping() -> None:
    assert set(NORMALIZERS) == {"djinni", "dou"}
    with pytest.raises(KeyError):
        normalize("workua", {})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Thu, 05 Nov 2026 16:10:00 +0200", datetime(2026, 11, 5, 14, 10, tzinfo=UTC)),
        ("Thu, 05 Nov 2026 16:10:00 -0000", None),  # no real offset
        ("not a date", None),
        (None, None),
    ],
)
def test_parse_pub_date(value: object, expected: datetime | None) -> None:
    assert parse_pub_date(value) == expected


def test_kyiv_date_is_the_rate_day() -> None:
    # 22:30 UTC on Nov 5 is already Nov 6 in Kyiv (UTC+2 in winter).
    assert kyiv_date(datetime(2026, 11, 5, 22, 30, tzinfo=UTC)) == date(2026, 11, 6)


# --- the normalizer version ------------------------------------------------------

# Every module a `vacancy` row is derived from.
PARSER_MODULES = (
    "dou_title.py",
    "money.py",
    "normalize.py",
    "salary.py",
    "seniority.py",
    "skills.py",
    "text.py",
    "work_format.py",
)
# The version and the parsers it was set for. Stored vacancies are rebuilt only
# when NORMALIZER_VERSION changes, so a parser change that ships without a new
# version leaves old rows parsed the old way.
PINNED_PARSERS = (3, "bb063381a7cb841b3cf0cc1dc1ba8edf078af7d463d089158820ac6c7c8de274")


def parsers_fingerprint() -> str:
    digest = hashlib.sha256()
    for name in PARSER_MODULES:
        digest.update(name.encode())
        digest.update(files("vacancy_radar.domain").joinpath(name).read_bytes())
    return digest.hexdigest()


def test_parser_changes_come_with_a_new_normalizer_version() -> None:
    """Fails whenever a parser module changes, so the version is a decision.

    If the change can alter any `vacancy` row (a pattern, a dictionary entry,
    a rounding rule), raise NORMALIZER_VERSION. If it cannot (a comment, a
    docstring), keep the version. Either way, pin the new fingerprint below.
    """
    assert (NORMALIZER_VERSION, parsers_fingerprint()) == PINNED_PARSERS
