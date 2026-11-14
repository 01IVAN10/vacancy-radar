from dataclasses import replace

import pytest

from vacancy_radar.domain.card import VacancyCard, details, format_salary, place, skill_names

CARD = VacancyCard(
    title="Senior Python Developer",
    url="https://djinni.co/jobs/1-senior-python/",
    source_name="Djinni",
    company="Acme",
    cities=["Kyiv"],
    salary_min=4000,
    salary_max=5500,
    salary_currency="USD",
    salary_period="month",
    seniority=["middle", "senior"],
    work_formats=["remote", "office"],
    skills=["python", "removed-skill", "django"],
)


@pytest.mark.parametrize(
    ("low", "high", "currency", "period", "shown"),
    [
        (4000, 5500, "USD", "month", "$4000-5500 a month"),
        (3000, None, "EUR", "month", "from €3000 a month"),
        (None, 90000, "UAH", "month", "up to ₴90000 a month"),
        (5000, 5000, "USD", "month", "$5000 a month"),
        (60000, 80000, "USD", "year", "$60000-80000 a year"),
        (40, None, "USD", "hour", "from $40 an hour"),
    ],
)
def test_formats_the_salary_as_stated(
    low: int | None, high: int | None, currency: str, period: str, shown: str
) -> None:
    card = replace(
        CARD, salary_min=low, salary_max=high, salary_currency=currency, salary_period=period
    )

    assert format_salary(card) == shown


@pytest.mark.parametrize(("currency", "period"), [(None, None), ("USD", None), (None, "month")])
def test_no_salary_without_currency_and_period(currency: str | None, period: str | None) -> None:
    assert format_salary(replace(CARD, salary_currency=currency, salary_period=period)) is None


def test_no_salary_without_an_amount() -> None:
    assert format_salary(replace(CARD, salary_min=None, salary_max=None)) is None


def test_details_are_levels_then_formats() -> None:
    assert details(CARD) == ["Middle", "Senior", "Remote", "Office"]


def test_skill_names_leave_out_skills_no_longer_in_the_dictionary() -> None:
    assert skill_names(CARD) == ["Python", "Django"]


@pytest.mark.parametrize(
    ("company", "cities", "shown"),
    [
        ("Acme", ["Kyiv", "Lviv"], "Acme, Kyiv, Lviv"),
        (None, ["Kyiv"], "Kyiv"),
        ("Acme", [], "Acme"),
        (None, [], ""),
    ],
)
def test_place_is_the_company_then_the_cities(
    company: str | None, cities: list[str], shown: str
) -> None:
    assert place(company, cities) == shown
