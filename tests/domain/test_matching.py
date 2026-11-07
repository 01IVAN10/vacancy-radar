from dataclasses import replace
from typing import Any

import pytest

from vacancy_radar.domain.matching import UsdRange, VacancyFacts, matches
from vacancy_radar.domain.subscriptions import SalaryFloor, SubscriptionFilter

# A filter that sets nothing. Real ones set at least one criterion, which
# `build_subscription` enforces; here each test sets the one it is about.
ANYTHING = SubscriptionFilter(
    skills=(), skills_match="any", seniority=(), work_formats=(), salary=None
)
# A vacancy where normalization found nothing at all.
BLANK = VacancyFacts(skills=frozenset(), seniority=(), work_formats=(), salary_usd=None)


def vacancy(**changes: Any) -> VacancyFacts:
    if "skills" in changes:
        changes["skills"] = frozenset(changes["skills"])
    return replace(BLANK, **changes)


def wanting(**changes: Any) -> SubscriptionFilter:
    return replace(ANYTHING, **changes)


def floor(min_usd: int, *, include_unsalaried: bool) -> SubscriptionFilter:
    return wanting(salary=SalaryFloor(min_usd, include_unsalaried))


def test_a_filter_without_criteria_matches_a_blank_vacancy() -> None:
    assert matches(ANYTHING, BLANK)


@pytest.mark.parametrize(
    ("wanted", "how", "found", "expected"),
    [
        (("python",), "any", {"python", "django"}, True),
        (("python",), "any", {"go"}, False),
        (("python", "go"), "any", {"go"}, True),
        (("python", "go"), "any", {"rust"}, False),
        (("python", "go"), "all", {"python", "go", "sql"}, True),
        (("python", "go"), "all", {"python"}, False),
        # Skills are the point of a skills filter: none found is no match.
        (("python",), "any", set(), False),
        (("python", "go"), "all", set(), False),
    ],
)
def test_skills(wanted: tuple[str, ...], how: str, found: set[str], expected: bool) -> None:
    criteria = wanting(skills=wanted, skills_match=how)

    assert matches(criteria, vacancy(skills=found)) is expected


def test_a_skill_that_left_the_dictionary_matches_nothing() -> None:
    criteria = wanting(skills=("cobol-removed",))

    assert not matches(criteria, vacancy(skills={"python", "go"}))


@pytest.mark.parametrize(
    ("wanted", "found", "expected"),
    [
        (("senior",), ("senior",), True),
        (("senior",), ("middle", "senior"), True),
        (("middle", "senior"), ("senior",), True),
        (("senior",), ("junior",), False),
        (("senior",), ("junior", "middle"), False),
        # A title without a level is unknown, not wrong.
        (("senior",), (), True),
    ],
)
def test_seniority(wanted: tuple[str, ...], found: tuple[str, ...], expected: bool) -> None:
    assert matches(wanting(seniority=wanted), vacancy(seniority=found)) is expected


@pytest.mark.parametrize(
    ("wanted", "found", "expected"),
    [
        (("remote",), ("remote",), True),
        (("remote",), ("remote", "office"), True),
        (("remote", "hybrid"), ("hybrid",), True),
        (("remote",), ("office",), False),
        (("remote",), ("hybrid", "office"), False),
        # A vacancy that names no format is unknown, not wrong.
        (("remote",), (), True),
    ],
)
def test_work_formats(wanted: tuple[str, ...], found: tuple[str, ...], expected: bool) -> None:
    assert matches(wanting(work_formats=wanted), vacancy(work_formats=found)) is expected


@pytest.mark.parametrize(
    ("salary", "expected"),
    [
        (UsdRange(2000, 4000), True),  # the top of the range reaches the minimum
        (UsdRange(3000, 3000), True),  # exactly the minimum
        (UsdRange(3000, None), True),  # "from $3,000"
        (UsdRange(3500, None), True),
        (UsdRange(2999, None), False),
        (UsdRange(None, 3000), True),  # "up to $3,000"
        (UsdRange(None, 2500), False),
        (UsdRange(1000, 2999), False),
    ],
)
def test_salary_against_a_minimum(salary: UsdRange, expected: bool) -> None:
    for include_unsalaried in (True, False):
        criteria = floor(3000, include_unsalaried=include_unsalaried)

        assert matches(criteria, vacancy(salary_usd=salary)) is expected


@pytest.mark.parametrize("include_unsalaried", [True, False])
def test_without_a_dollar_figure_the_subscription_decides(include_unsalaried: bool) -> None:
    # No salary stated, an hourly rate and a salary not converted yet all
    # reach matching as `salary_usd=None`.
    criteria = floor(3000, include_unsalaried=include_unsalaried)

    assert matches(criteria, vacancy(salary_usd=None)) is include_unsalaried


def test_without_a_minimum_any_salary_matches() -> None:
    assert matches(wanting(seniority=("senior",)), vacancy(salary_usd=UsdRange(None, 500)))


def test_every_criterion_must_hold() -> None:
    criteria = SubscriptionFilter(
        skills=("python", "django"),
        skills_match="all",
        seniority=("senior",),
        work_formats=("remote",),
        salary=SalaryFloor(4000, include_unsalaried=False),
    )
    fitting = vacancy(
        skills={"python", "django", "postgresql"},
        seniority=("senior",),
        work_formats=("remote",),
        salary_usd=UsdRange(4000, 6000),
    )

    assert matches(criteria, fitting)
    assert not matches(criteria, replace(fitting, skills=frozenset({"python"})))
    assert not matches(criteria, replace(fitting, seniority=("junior",)))
    assert not matches(criteria, replace(fitting, work_formats=("office",)))
    assert not matches(criteria, replace(fitting, salary_usd=UsdRange(None, 3000)))
    assert not matches(criteria, replace(fitting, salary_usd=None))


def test_unknown_fields_still_need_the_known_ones_to_hold() -> None:
    criteria = wanting(skills=("python",), seniority=("senior",), work_formats=("remote",))

    assert matches(criteria, vacancy(skills={"python"}))
    assert not matches(criteria, vacancy(skills={"java"}))


@pytest.mark.parametrize(
    ("min_usd", "max_usd", "top"),
    [(2000, 4000, 4000), (3000, None, 3000), (None, 2500, 2500), (3000, 3000, 3000)],
)
def test_usd_range_top(min_usd: int | None, max_usd: int | None, top: int) -> None:
    assert UsdRange(min_usd, max_usd).top == top


@pytest.mark.parametrize(("min_usd", "max_usd"), [(None, None), (4000, 3000)])
def test_usd_range_refuses_impossible_bounds(min_usd: int | None, max_usd: int | None) -> None:
    with pytest.raises(ValueError, match="salary range"):
        UsdRange(min_usd, max_usd)
