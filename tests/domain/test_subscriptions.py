from collections.abc import Sequence
from typing import Any

import pytest

from vacancy_radar.domain.skills import SKILLS
from vacancy_radar.domain.subscriptions import (
    MAX_MIN_SALARY_USD,
    MAX_NAME_LENGTH,
    MAX_SKILLS,
    InvalidSubscriptionError,
    SubscriptionDraft,
    SubscriptionFilter,
    build_subscription,
)


def build(**overrides: Any) -> SubscriptionDraft:
    values: dict[str, Any] = {
        "name": "Python remote",
        "skills": ["python"],
        "skills_match": "any",
        "seniority": [],
        "work_formats": [],
        "min_salary_usd": "",
        "include_unsalaried": True,
    }
    return build_subscription(**(values | overrides))


def errors_of(**overrides: Any) -> dict[str, str]:
    with pytest.raises(InvalidSubscriptionError) as caught:
        build(**overrides)
    return caught.value.errors


def test_builds_a_clean_draft() -> None:
    draft = build(
        name="  Python   remote ",
        skills=["django", "python", "python"],
        skills_match="all",
        seniority=["senior", "middle"],
        work_formats=["office", "remote"],
        min_salary_usd=" 3 000 ",
        include_unsalaried=False,
    )

    assert draft == SubscriptionDraft(
        name="Python remote",
        filter=SubscriptionFilter(
            # Without duplicates, in the order of each dictionary.
            skills=("python", "django"),
            skills_match="all",
            seniority=("middle", "senior"),
            work_formats=("remote", "office"),
            min_salary_usd=3000,
            include_unsalaried=False,
        ),
    )


@pytest.mark.parametrize(
    "criteria",
    [
        {"skills": ["python"]},
        {"seniority": ["junior"]},
        {"work_formats": ["remote"]},
        {"min_salary_usd": "2500"},
    ],
)
def test_one_criterion_is_enough(criteria: dict[str, Any]) -> None:
    empty = {"skills": [], "seniority": [], "work_formats": [], "min_salary_usd": ""}

    draft = build(**(empty | criteria))

    assert draft.name == "Python remote"


def test_a_filter_without_criteria_is_refused() -> None:
    errors = errors_of(skills=[], seniority=[], work_formats=[], min_salary_usd="")

    assert errors == {"filter": "Choose at least one skill, level, work format or minimum salary."}


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "Give the subscription a name."),
        ("   ", "Give the subscription a name."),
        ("x" * (MAX_NAME_LENGTH + 1), "at most 80 characters"),
    ],
)
def test_name_is_required_and_bounded(name: str, message: str) -> None:
    assert message in errors_of(name=name)["name"]


def test_name_of_maximum_length_is_accepted() -> None:
    assert build(name="x" * MAX_NAME_LENGTH).name == "x" * MAX_NAME_LENGTH


def test_unknown_skills_are_refused_not_dropped() -> None:
    assert errors_of(skills=["python", "cobol-on-wheels"]) == {
        "skills": "Unknown skills: cobol-on-wheels."
    }


def test_skill_count_is_limited() -> None:
    slugs = [skill.slug for skill in SKILLS]

    assert len(build(skills=slugs[:MAX_SKILLS]).filter.skills) == MAX_SKILLS
    assert "at most 20 skills" in errors_of(skills=slugs[: MAX_SKILLS + 1])["skills"]


@pytest.mark.parametrize("value", ["", "some", "ALL"])
def test_skills_match_must_be_any_or_all(value: str) -> None:
    assert "skills_match" in errors_of(skills_match=value)


@pytest.mark.parametrize(
    ("field", "values"),
    [("seniority", ["senior", "guru"]), ("work_formats", ["remote", "beach"])],
)
def test_unknown_levels_and_formats_are_refused(field: str, values: Sequence[str]) -> None:
    assert errors_of(**{field: values})[field] == f"Unknown values: {values[1]}."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", None), ("1", 1), ("3,500", 3500), (str(MAX_MIN_SALARY_USD), MAX_MIN_SALARY_USD)],
)
def test_min_salary_accepts_whole_dollars(raw: str, expected: int | None) -> None:
    assert build(min_salary_usd=raw).filter.min_salary_usd == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("abc", "whole number"),
        ("3000.50", "whole number"),
        ("-100", "whole number"),
        ("٣٠٠٠", "whole number"),  # Arabic-Indic digits pass str.isdigit()
        ("0", "from 1 to 100,000"),
        (str(MAX_MIN_SALARY_USD + 1), "from 1 to 100,000"),
    ],
)
def test_min_salary_refuses_other_input(raw: str, message: str) -> None:
    assert message in errors_of(min_salary_usd=raw)["min_salary_usd"]


def test_every_invalid_field_is_reported_at_once() -> None:
    errors = errors_of(name="", skills=["nope"], skills_match="x", min_salary_usd="abc")

    assert set(errors) == {"name", "skills", "skills_match", "min_salary_usd"}


def test_error_message_names_its_fields() -> None:
    error = InvalidSubscriptionError({"name": "Give the subscription a name."})

    assert str(error) == "name: Give the subscription a name."
    with pytest.raises(ValueError, match="at least one field error"):
        InvalidSubscriptionError({})
