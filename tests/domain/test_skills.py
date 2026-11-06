"""Skill extraction, tested on phrases from collected vacancies (2026-11-05/06)."""

import re

import pytest

from tests.support import fixture_bytes
from vacancy_radar.domain.skills import SKILLS, SKILLS_BY_SLUG, Skill, extract_skills
from vacancy_radar.domain.text import html_to_text, normalize_text
from vacancy_radar.sources import djinni


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "Сильний backend (Python, Go, Java, Rust, Node.js).",
            ("go", "java", "nodejs", "python", "rust"),
            id="backend-list",
        ),
        pytest.param(
            "Senior Networking Engineer (C/C++, Python)", ("c", "cpp", "python"), id="c-cpp"
        ),
        pytest.param(
            "Знання MS SQL Server (T-SQL), SQLite, Oracle", ("mssql", "oracle", "sql"), id="sql"
        ),
        pytest.param(
            "Stack: Next.js 16 App Router, React 19 with React Compiler, TypeScript",
            ("nextjs", "react", "typescript"),
            id="frontend",
        ),
        pytest.param("Strong knowledge of C# and .NET Core", ("csharp", "dotnet"), id="csharp"),
        pytest.param("ASP.NET MVC", ("dotnet",), id="asp-net"),
        pytest.param("Java, Spring Boot (JPA, Data, Web), Maven", ("java", "spring"), id="spring"),
        pytest.param(
            "Kubernetes (EKS), Terraform, ArgoCD",
            ("argocd", "kubernetes", "terraform"),
            id="devops",
        ),
        pytest.param("Familiarity with RESTful APIs", ("rest",), id="restful"),
        pytest.param("Python, R, SQL для аналізу даних", ("python", "r", "sql"), id="r-in-list"),
        pytest.param("Advanced Excel skills", ("excel",), id="excel"),
        pytest.param("Unity 3D Engineer / Gamedev Developer", ("unity",), id="unity"),
        pytest.param("Swift and SwiftUI", ("swift",), id="swift"),
        pytest.param("PySpark and Databricks", ("databricks", "spark"), id="pyspark"),
        pytest.param(
            "Node.js, React and React Native",
            ("nodejs", "react", "react-native"),
            id="react-and-native",
        ),
        pytest.param("React Native developer", ("react-native",), id="react-native-only"),
        pytest.param(
            "Embedded Linux (Yocto, Buildroot)", ("embedded-linux", "linux"), id="embedded-linux"
        ),
        pytest.param("Golang is a strong plus", ("go",), id="golang"),
        # Words that only look like a skill.
        pytest.param(
            "native iOS SDKs (Swift / Objective-C).", ("ios", "objective-c", "swift"), id="objc"
        ),
        pytest.param("Інженер-пілот БпЛА (R&D)", (), id="r-and-d"),
        pytest.param("R/D Engineer", (), id="r-slash-d"),
        pytest.param("communicating with founders, C-level executives", (), id="c-level"),
        pytest.param("brokerage models (A-Book, B-Book, C-Book, STP)", (), id="c-book"),
        pytest.param("Менеджер з компенсацій та пільг (C&B)", (), id="c-and-b"),
        pytest.param("Go live with the new product", (), id="go-verb"),
        pytest.param("Let's go-to-market together", (), id="go-to-market"),
        pytest.param("SWIFT payments and ISO 20022", (), id="swift-bank-network"),
        pytest.param("you excel at communication", (), id="excel-verb"),
        pytest.param("access control, data lineage (Unity Catalog)", (), id="unity-catalog"),
        pytest.param("the rest of the team", (), id="rest-word"),
        pytest.param("Prepare the Go/No-Go decision for each release", (), id="go-no-go"),
        pytest.param("Weekly RAG status reports to stakeholders", (), id="rag-status"),
        pytest.param("Calibration of airflow sensors on the UAV", (), id="airflow-word"),
        pytest.param("Airflow, dbt, BigQuery", ("airflow", "bigquery", "dbt"), id="airflow"),
        pytest.param("PostgreSQL", ("postgresql",), id="sql-inside-postgresql"),
        pytest.param("Node.js", ("nodejs",), id="js-inside-nodejs"),
        pytest.param("JavaScript", ("javascript",), id="java-inside-javascript"),
        pytest.param("", (), id="empty"),
    ],
)
def test_extract_skills(text: str, expected: tuple[str, ...]) -> None:
    assert extract_skills(text) == expected


def test_extracts_skills_from_fixture_vacancies() -> None:
    items = djinni.parse_feed(fixture_bytes("djinni/rss_python.xml")).items
    found = {
        item.external_id: extract_skills(
            normalize_text(item.payload["title"]) + " " + html_to_text(item.payload["description"])
        )
        for item in items
    }

    # Every Python feed vacancy names Python; the titles name more.
    assert all("python" in skills for skills in found.values())
    assert {"javascript", "ios"} <= set(found["856641"])  # "(Python, JavaScript and iOS SDK)"
    assert {"react"} <= set(found["832898"])  # "(Python and React)"
    assert {"gcp"} <= set(found["852433"])  # "Senior Python / GCP Data Engineer"


# --- the dictionary itself -------------------------------------------------------


def test_slugs_are_unique_and_url_safe() -> None:
    slugs = [skill.slug for skill in SKILLS]

    assert len(slugs) == len(set(slugs))
    assert all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) for slug in slugs)
    assert set(SKILLS_BY_SLUG) == set(slugs)


def test_no_spelling_belongs_to_two_skills() -> None:
    owners: dict[str, set[str]] = {}
    for skill in SKILLS:
        for spelling in skill.aliases + skill.exact + skill.listed:
            owners.setdefault(spelling.lower(), set()).add(skill.slug)

    assert {s: o for s, o in owners.items() if len(o) > 1} == {}


@pytest.mark.parametrize("skill", SKILLS, ids=lambda skill: skill.slug)
def test_every_skill_is_found_by_its_first_spelling(skill: Skill) -> None:
    spelling = (skill.aliases + skill.exact + skill.listed)[0]
    # A `listed` spelling counts only inside a list.
    text = spelling if skill.aliases or skill.exact else f"Python, {spelling}, SQL"

    assert skill.slug in extract_skills(text)
