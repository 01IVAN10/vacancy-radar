from decimal import Decimal

import pytest

from vacancy_radar.domain.money import RateCurrency, to_monthly_usd
from vacancy_radar.domain.salary import Salary

# Official NBU rates for 2026-11-05 (tests/fixtures/nbu/).
RATES: dict[RateCurrency, Decimal] = {"USD": Decimal("45.5375"), "EUR": Decimal("51.2489")}


@pytest.mark.parametrize(
    ("salary", "expected"),
    [
        pytest.param(Salary(2000, 3000, "USD"), (2000, 3000), id="usd-month"),
        # 40 000 / 45.5375 = 878.4, 60 000 / 45.5375 = 1317.6
        pytest.param(Salary(40_000, 60_000, "UAH"), (878, 1318), id="uah-month"),
        # 750 × 51.2489 / 45.5375 = 844.07, through hryvnias
        pytest.param(Salary(750, 750, "EUR"), (844, 844), id="eur-cross-rate"),
        pytest.param(Salary(None, 60_000, "USD", "year"), (None, 5000), id="usd-year"),
        # 1 200 000 / 12 / 45.5375 = 2195.99
        pytest.param(Salary(1_200_000, None, "UAH", "year"), (2196, None), id="uah-year"),
        pytest.param(Salary(None, 2000, "USD"), (None, 2000), id="open-lower-bound"),
        pytest.param(Salary(None, 25, "USD", "hour"), (None, None), id="hourly-has-no-month"),
    ],
)
def test_to_monthly_usd(salary: Salary, expected: tuple[int | None, int | None]) -> None:
    assert to_monthly_usd(salary, RATES) == expected


def test_usd_needs_no_rates() -> None:
    assert to_monthly_usd(Salary(1000, 1500, "USD"), None) == (1000, 1500)


@pytest.mark.parametrize(
    ("salary", "rates"),
    [
        pytest.param(Salary(40_000, 60_000, "UAH"), None, id="uah-without-rates"),
        pytest.param(Salary(750, 750, "EUR"), {"USD": Decimal("45.5375")}, id="eur-without-eur"),
    ],
)
def test_missing_rate_gives_no_value(
    salary: Salary, rates: dict[RateCurrency, Decimal] | None
) -> None:
    assert to_monthly_usd(salary, rates) == (None, None)


def test_rounds_half_up() -> None:
    assert to_monthly_usd(Salary(1, 3, "UAH"), {"USD": Decimal(2)}) == (1, 2)
