from collections.abc import Iterable
from dataclasses import replace

import pytest

from vacancy_radar.domain.matching import VacancyFacts, matches
from vacancy_radar.domain.money import UsdRange
from vacancy_radar.domain.seniority import Level
from vacancy_radar.domain.subscriptions import SalaryFloor, SkillsMatch, SubscriptionFilter
from vacancy_radar.domain.work_format import WorkFormat


def vacancy(
    *,
    skills: Iterable[str] = (),
    seniority: tuple[Level, ...] = (),
    work_formats: tuple[WorkFormat, ...] = (),
    salary_usd: UsdRange | None = None,
) -> VacancyFacts:
    """A vacancy that states only what is given: by default, normalization found nothing."""
    return VacancyFacts(
        skills=frozenset(skills),
        seniority=seniority,
        work_formats=work_formats,
        salary_usd=salary_usd,
    )


def wanting(
    *,
    skills: tuple[str, ...] = (),
    skills_match: SkillsMatch = "any",
    seniority: tuple[Level, ...] = (),
    work_formats: tuple[WorkFormat, ...] = (),
    salary: SalaryFloor | None = None,
) -> SubscriptionFilter:
    """A filter that sets only what is given."""
    return SubscriptionFilter(
        skills=skills,
        skills_match=skills_match,
        seniority=seniority,
        work_formats=work_formats,
        salary=salary,
    )


def floor(min_usd: int, *, include_unsalaried: bool) -> SubscriptionFilter:
    return wanting(salary=SalaryFloor(min_usd, include_unsalaried))


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
def test_skills(wanted: tuple[str, ...], how: SkillsMatch, found: set[str], expected: bool) -> None:
    criteria = wanting(skills=wanted, skills_match=how)

    assert matches(criteria, vacancy(skills=found)) is expected


@pytest.mark.parametrize(
    ("wanted", "found", "expected"),
    [
        (("senior",), ("senior",), True),
        (("senior",), ("middle", "senior"), True),
        (("middle", "senior"), ("senior",), True),
        (("senior",), ("junior",), False),
        (("senior",), ("junior", "middle"), False),
    ],
)
def test_seniority(wanted: tuple[Level, ...], found: tuple[Level, ...], expected: bool) -> None:
    assert matches(wanting(seniority=wanted), vacancy(seniority=found)) is expected


@pytest.mark.parametrize(
    ("wanted", "found", "expected"),
    [
        (("remote",), ("remote",), True),
        (("remote",), ("remote", "office"), True),
        (("remote", "hybrid"), ("hybrid",), True),
        (("remote",), ("office",), False),
        (("remote",), ("hybrid", "office"), False),
    ],
)
def test_work_formats(
    wanted: tuple[WorkFormat, ...], found: tuple[WorkFormat, ...], expected: bool
) -> None:
    assert matches(wanting(work_formats=wanted), vacancy(work_formats=found)) is expected


@pytest.mark.parametrize("include_unsalaried", [True, False])
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
def test_a_stated_salary_against_a_minimum(
    salary: UsdRange, expected: bool, include_unsalaried: bool
) -> None:
    criteria = floor(3000, include_unsalaried=include_unsalaried)

    assert matches(criteria, vacancy(salary_usd=salary)) is expected


# --- what the vacancy does not state ------------------------------------------


@pytest.mark.parametrize(
    "criteria",
    [
        pytest.param(wanting(seniority=("senior",)), id="seniority"),
        pytest.param(wanting(work_formats=("remote",)), id="work-format"),
        pytest.param(wanting(seniority=("senior",), work_formats=("remote",)), id="both"),
        pytest.param(floor(3000, include_unsalaried=True), id="salary-including-unsalaried"),
        pytest.param(
            wanting(
                seniority=("senior",),
                work_formats=("remote",),
                salary=SalaryFloor(3000, include_unsalaried=True),
            ),
            id="all-three",
        ),
    ],
)
def test_unknown_values_alone_never_match(criteria: SubscriptionFilter) -> None:
    # Seniority, format and salary are missing from most vacancies. If unknown
    # counted as a match, these subscriptions would get nearly every vacancy.
    assert not matches(criteria, vacancy())


@pytest.mark.parametrize(
    ("criteria", "fitting"),
    [
        pytest.param(
            wanting(skills=("python",), seniority=("senior",)),
            vacancy(skills={"python"}),
            id="level-unknown",
        ),
        pytest.param(
            wanting(seniority=("senior",), work_formats=("remote",)),
            vacancy(seniority=("senior",)),
            id="format-unknown",
        ),
        pytest.param(
            wanting(work_formats=("remote",), salary=SalaryFloor(3000, include_unsalaried=True)),
            vacancy(work_formats=("remote",)),
            id="salary-unknown",
        ),
    ],
)
def test_unknown_values_do_not_rule_out_a_vacancy_another_criterion_meets(
    criteria: SubscriptionFilter, fitting: VacancyFacts
) -> None:
    assert matches(criteria, fitting)


def test_without_a_dollar_figure_excluding_unsalaried_rules_the_vacancy_out() -> None:
    # No salary stated, an hourly rate and a salary not converted yet all
    # reach matching as `salary_usd=None`.
    criteria = wanting(work_formats=("remote",), salary=SalaryFloor(3000, include_unsalaried=False))

    assert not matches(criteria, vacancy(work_formats=("remote",)))


def test_a_known_mismatch_rules_out_a_vacancy_other_criteria_meet() -> None:
    criteria = wanting(skills=("python",), seniority=("senior",), work_formats=("remote",))

    assert matches(criteria, vacancy(skills={"python"}, seniority=("senior",)))
    assert not matches(criteria, vacancy(skills={"python"}, seniority=("junior",)))
    assert not matches(criteria, vacancy(skills={"python"}, work_formats=("office",)))
    assert not matches(criteria, vacancy(skills={"java"}, seniority=("senior",)))


def test_without_a_minimum_any_salary_matches() -> None:
    criteria = wanting(seniority=("senior",))

    assert matches(criteria, vacancy(seniority=("senior",), salary_usd=UsdRange(None, 500)))


def test_a_filter_without_criteria_matches_nothing() -> None:
    # `build_subscription` never stores one; matching does not rely on it.
    everything = vacancy(
        skills={"python"},
        seniority=("senior",),
        work_formats=("remote",),
        salary_usd=UsdRange(5000, None),
    )

    assert not matches(wanting(), everything)


def test_every_criterion_must_hold() -> None:
    criteria = wanting(
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


# --- from stored rows -----------------------------------------------------------


def test_facts_from_a_stored_row() -> None:
    facts = VacancyFacts.from_stored(
        skills=["python", "django"],
        seniority=["middle", "senior"],
        work_formats=["remote"],
        salary_usd_min=None,
        salary_usd_max=4000,
    )

    assert facts == vacancy(
        skills={"python", "django"},
        seniority=("middle", "senior"),
        work_formats=("remote",),
        salary_usd=UsdRange(None, 4000),
    )


def test_facts_from_a_stored_row_without_a_dollar_figure() -> None:
    facts = VacancyFacts.from_stored(
        skills=[], seniority=[], work_formats=[], salary_usd_min=None, salary_usd_max=None
    )

    assert facts == vacancy()


@pytest.mark.parametrize(
    ("seniority", "work_formats", "salary_usd_min", "salary_usd_max", "error"),
    [
        pytest.param(["guru"], [], None, None, "guru", id="unknown-level"),
        pytest.param([], ["moon"], None, None, "moon", id="unknown-format"),
        pytest.param([], [], 4000, 3000, "min_usd", id="reversed-range"),
    ],
)
def test_facts_refuse_values_the_database_keeps_out(
    seniority: list[str],
    work_formats: list[str],
    salary_usd_min: int | None,
    salary_usd_max: int | None,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        VacancyFacts.from_stored(
            skills=[],
            seniority=seniority,
            work_formats=work_formats,
            salary_usd_min=salary_usd_min,
            salary_usd_max=salary_usd_max,
        )
