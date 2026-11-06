"""Seniority detection, tested on titles of collected vacancies (2026-11-05/06)."""

import pytest

from vacancy_radar.domain.seniority import LEVELS, Level, detect_seniority


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        pytest.param("Senior Python Engineer", ("senior",), id="senior"),
        pytest.param("Junior Amazon PPC Manager", ("junior",), id="junior"),
        pytest.param("Strong Middle Project Manager", ("middle",), id="strong-middle"),
        pytest.param("Intern Full Stack Engineer (React + Node)", ("intern",), id="intern"),
        pytest.param("Trainee Media Buyer / Marketing Manager", ("intern",), id="trainee"),
        pytest.param("Talent Sourcer / Jr Recruiter", ("junior",), id="jr"),
        pytest.param("QA-інженер — Strong Junior", ("junior",), id="strong-junior"),
        pytest.param("Software Engineer (Middle)", ("middle",), id="in-parentheses"),
        # Several levels: the vacancy matches subscriptions to each of them.
        pytest.param(
            "Strong Junior / Middle Full-Stack Developer (Python and React)",
            ("junior", "middle"),
            id="junior-middle",
        ),
        pytest.param(
            "Junior to Mid Data Scientist/Analyst", ("junior", "middle"), id="junior-to-mid"
        ),
        pytest.param(
            "Middle + / Senior Mobile iOS Developer in Mobile System Team (#1204)",
            ("middle", "senior"),
            id="middle-plus-senior",
        ),
        pytest.param(
            "Senior, middle hardware engineer, інженер-електронщик",
            ("middle", "senior"),
            id="listed-out-of-order",
        ),
        pytest.param("Junior/Trainee Hardware Engineer", ("intern", "junior"), id="slash"),
        pytest.param(
            "Regular/Senior Web Graphics Developer", ("middle", "senior"), id="regular-is-middle"
        ),
        pytest.param(
            "Senior / Lead PHP Engineer with AI skills (#5887)",
            ("senior", "lead"),
            id="senior-lead",
        ),
        pytest.param("Senior/Staff DevOps Engineer", ("senior", "lead"), id="staff"),
        # Leadership roles.
        pytest.param("Team Lead Python", ("lead",), id="team-lead"),
        pytest.param("DevOps Tech Lead", ("lead",), id="tech-lead"),
        pytest.param("Head of AI", ("lead",), id="head-of"),
        pytest.param(
            "Principal Software Engineer — AI-driven Development", ("lead",), id="principal"
        ),
        pytest.param("Chief Technology Officer (CTO, MilTech)", ("lead",), id="chief"),
        pytest.param("VP of Engineering", ("lead",), id="vp"),
        pytest.param("Операційний директор", ("lead",), id="dyrektor"),
        pytest.param(
            "Керівник розробки та технічного розвитку продуктів", ("lead",), id="kerivnyk"
        ),
        pytest.param("Провідний інженер-конструктор (БПЛА)", ("senior",), id="providnyi"),
        # No level.
        pytest.param("Lead Generation Specialist (LinkedIn)", (), id="lead-generation"),
        pytest.param("Internal Communications Specialist", (), id="internal-not-intern"),
        pytest.param("International Contract Lawyer", (), id="international"),
        pytest.param("Mid-Market Account Executive", (), id="mid-market"),
        pytest.param("Data Architect", (), id="architect-has-no-level"),
        pytest.param("Product Manager", (), id="no-level"),
        pytest.param("", (), id="empty"),
    ],
)
def test_detect_seniority(title: str, expected: tuple[Level, ...]) -> None:
    assert detect_seniority(title) == expected


def test_levels_are_ordered_from_least_to_most_senior() -> None:
    assert LEVELS == ("intern", "junior", "middle", "senior", "lead")
