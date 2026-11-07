from dataclasses import replace
from typing import Any

import pytest

from vacancy_radar.domain.skills import SKILLS
from vacancy_radar.domain.subscriptions import (
    MAX_MIN_SALARY_USD,
    MAX_NAME_LENGTH,
    MAX_SKILLS,
    InvalidSubscriptionError,
    SalaryFloor,
    SubscriptionDraft,
    SubscriptionFilter,
    SubscriptionForm,
    build_subscription,
)

PYTHON = SubscriptionForm(name="Python remote", skills=frozenset({"python"}))


def build(**changes: Any) -> SubscriptionDraft:
    return build_subscription(replace(PYTHON, **changes))


def errors_of(**changes: Any) -> dict[str, str]:
    with pytest.raises(InvalidSubscriptionError) as caught:
        build(**changes)
    return caught.value.errors


def test_builds_a_clean_draft() -> None:
    draft = build(
        name="  Python   remote ",
        skills=frozenset({"django", "python"}),
        skills_match="all",
        seniority=frozenset({"senior", "middle"}),
        work_formats=frozenset({"office", "remote"}),
        min_salary_usd=" 3 000 ",
        include_unsalaried=False,
    )

    assert draft == SubscriptionDraft(
        name="Python remote",
        filter=SubscriptionFilter(
            # In the order of each dictionary.
            skills=("python", "django"),
            skills_match="all",
            seniority=("middle", "senior"),
            work_formats=("remote", "office"),
            salary=SalaryFloor(min_usd=3000, include_unsalaried=False),
        ),
    )


@pytest.mark.parametrize(
    "criteria",
    [
        {"skills": frozenset({"python"})},
        {"seniority": frozenset({"junior"})},
        {"work_formats": frozenset({"remote"})},
        {"min_salary_usd": "2500"},
    ],
)
def test_one_criterion_is_enough(criteria: dict[str, Any]) -> None:
    draft = build(**({"skills": frozenset()} | criteria))

    assert draft.name == "Python remote"


def test_a_filter_without_criteria_is_refused() -> None:
    assert errors_of(skills=frozenset()) == {
        "filter": "Choose at least one skill, level, work format or minimum salary."
    }


# --- canonical form: settings that change nothing are not stored


@pytest.mark.parametrize("include_unsalaried", [True, False])
def test_include_unsalaried_exists_only_with_a_minimum(include_unsalaried: bool) -> None:
    # A ticked or unticked box with no minimum gives the same filter, so an
    # unticked box cannot linger unseen and apply once a minimum is added.
    draft = build(min_salary_usd="", include_unsalaried=include_unsalaried)

    assert draft.filter.salary is None
    assert draft == build(min_salary_usd="", include_unsalaried=True)


@pytest.mark.parametrize("skills", [frozenset(), frozenset({"python"})])
def test_skills_match_is_any_with_fewer_than_two_skills(skills: frozenset[str]) -> None:
    draft = build(skills=skills, seniority=frozenset({"senior"}), skills_match="all")

    assert draft.filter.skills_match == "any"
    assert draft == build(skills=skills, seniority=frozenset({"senior"}), skills_match="any")


def test_form_of_a_filter_round_trips() -> None:
    draft = build(
        skills=frozenset({"python", "go"}),
        skills_match="all",
        min_salary_usd="4000",
        include_unsalaried=False,
    )

    form = SubscriptionForm.of(draft.name, draft.filter)

    assert build_subscription(form) == draft
    assert form.min_salary_usd == "4000"


def test_form_of_a_filter_without_salary_suggests_including_unsalaried() -> None:
    form = SubscriptionForm.of("x", build().filter)

    assert (form.min_salary_usd, form.include_unsalaried) == ("", True)


# --- field rules


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
    assert errors_of(skills=frozenset({"python", "cobol-on-wheels"})) == {
        "skills": "Unknown skills: cobol-on-wheels."
    }


def test_skill_count_is_limited() -> None:
    slugs = [skill.slug for skill in SKILLS]

    assert len(build(skills=frozenset(slugs[:MAX_SKILLS])).filter.skills) == MAX_SKILLS
    assert "at most 20 skills" in errors_of(skills=frozenset(slugs[: MAX_SKILLS + 1]))["skills"]


@pytest.mark.parametrize("value", ["", "some", "ALL"])
def test_skills_match_must_be_any_or_all(value: str) -> None:
    assert "skills_match" in errors_of(skills_match=value)


@pytest.mark.parametrize(
    ("field", "values", "unknown"),
    [
        ("seniority", frozenset({"senior", "guru"}), "guru"),
        ("work_formats", frozenset({"remote", "beach"}), "beach"),
    ],
)
def test_unknown_levels_and_formats_are_refused(
    field: str, values: frozenset[str], unknown: str
) -> None:
    assert errors_of(**{field: values})[field] == f"Unknown values: {unknown}."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1),
        ("3000", 3000),
        (" 3,500 ", 3500),
        ("3 500", 3500),
        (str(MAX_MIN_SALARY_USD), MAX_MIN_SALARY_USD),
        ("100,000", MAX_MIN_SALARY_USD),
    ],
)
def test_min_salary_accepts_whole_dollars(raw: str, expected: int) -> None:
    assert build(min_salary_usd=raw).filter.salary == SalaryFloor(expected, True)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("abc", "whole number"),
        ("3000.50", "whole number"),
        ("-100", "whole number"),
        ("3,0,0", "whole number"),  # commas only between thousands
        ("30,00", "whole number"),
        ("3,000 000", "whole number"),  # one kind of separator
        ("٣٠٠٠", "whole number"),  # Arabic-Indic digits pass str.isdigit()
        ("9" * 5000, "whole number"),  # never reaches int(), which refuses > 4300 digits
        ("0", "from 1 to 100,000"),
        (str(MAX_MIN_SALARY_USD + 1), "from 1 to 100,000"),
        ("1,000,000", "from 1 to 100,000"),
    ],
)
def test_min_salary_refuses_other_input(raw: str, message: str) -> None:
    assert message in errors_of(min_salary_usd=raw)["min_salary_usd"]


def test_every_invalid_field_is_reported_at_once() -> None:
    errors = errors_of(name="", skills=frozenset({"nope"}), skills_match="x", min_salary_usd="abc")

    assert set(errors) == {"name", "skills", "skills_match", "min_salary_usd"}


def test_error_message_names_its_fields() -> None:
    error = InvalidSubscriptionError({"name": "Give the subscription a name."})

    assert str(error) == "name: Give the subscription a name."
    with pytest.raises(ValueError, match="at least one field error"):
        InvalidSubscriptionError({})


# --- rebuilding a filter from stored values

STORED: dict[str, Any] = {
    "skills": ["python", "django"],
    "skills_match": "all",
    "seniority": ["senior"],
    "work_formats": ["remote"],
    "min_salary_usd": 3000,
    "include_unsalaried": False,
}


def test_from_stored_types_the_values() -> None:
    assert SubscriptionFilter.from_stored(**STORED) == SubscriptionFilter(
        skills=("python", "django"),
        skills_match="all",
        seniority=("senior",),
        work_formats=("remote",),
        salary=SalaryFloor(3000, include_unsalaried=False),
    )


def test_from_stored_keeps_skills_that_left_the_dictionary() -> None:
    # Dropping it would widen the filter; kept, it matches nothing.
    criteria = SubscriptionFilter.from_stored(**(STORED | {"skills": ["retired-skill"]}))

    assert criteria.skills == ("retired-skill",)


@pytest.mark.parametrize(
    "changes",
    [
        {"skills_match": "most"},
        {"seniority": ["guru"]},
        {"work_formats": ["beach"]},
        {"include_unsalaried": None},
        {"min_salary_usd": None},
    ],
)
def test_from_stored_refuses_values_the_constraints_keep_out(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - each case has its own message
        SubscriptionFilter.from_stored(**(STORED | changes))


def test_from_stored_without_salary() -> None:
    stored = STORED | {"min_salary_usd": None, "include_unsalaried": None}

    assert SubscriptionFilter.from_stored(**stored).salary is None
