from decimal import Decimal

import pytest

from vacancy_radar.domain.money import RateCurrency, UsdRange, to_monthly_usd
from vacancy_radar.domain.salary import Salary

# Official NBU rates for 2026-11-05 (tests/fixtures/nbu/).
RATES: dict[RateCurrency, Decimal] = {"USD": Decimal("45.5375"), "EUR": Decimal("51.2489")}


@pytest.mark.parametrize(
    ("salary", "expected"),
    [
        pytest.param(Salary(2000, 3000, "USD"), UsdRange(2000, 3000), id="usd-month"),
        # 40 000 / 45.5375 = 878.4, 60 000 / 45.5375 = 1317.6
        pytest.param(Salary(40_000, 60_000, "UAH"), UsdRange(878, 1318), id="uah-month"),
        # 750 × 51.2489 / 45.5375 = 844.07, through hryvnias
        pytest.param(Salary(750, 750, "EUR"), UsdRange(844, 844), id="eur-cross-rate"),
        pytest.param(Salary(None, 60_000, "USD", "year"), UsdRange(None, 5000), id="usd-year"),
        # 1 200 000 / 12 / 45.5375 = 2195.99
        pytest.param(Salary(1_200_000, None, "UAH", "year"), UsdRange(2196, None), id="uah-year"),
        pytest.param(Salary(None, 2000, "USD"), UsdRange(None, 2000), id="open-lower-bound"),
        pytest.param(Salary(None, 25, "USD", "hour"), None, id="hourly-has-no-month"),
    ],
)
def test_to_monthly_usd(salary: Salary, expected: UsdRange | None) -> None:
    assert to_monthly_usd(salary, RATES) == expected


def test_usd_needs_no_rates() -> None:
    assert to_monthly_usd(Salary(1000, 1500, "USD"), None) == UsdRange(1000, 1500)


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
    assert to_monthly_usd(salary, rates) is None


def test_rounds_half_up() -> None:
    assert to_monthly_usd(Salary(1, 3, "UAH"), {"USD": Decimal(2)}) == UsdRange(1, 2)


@pytest.mark.parametrize(
    ("min_usd", "max_usd", "top"),
    [(2000, 4000, 4000), (3000, None, 3000), (None, 2500, 2500), (3000, 3000, 3000)],
)
def test_usd_range_top(min_usd: int | None, max_usd: int | None, top: int) -> None:
    assert UsdRange(min_usd, max_usd).top == top


@pytest.mark.parametrize(
    ("min_usd", "max_usd", "error"),
    [(None, None, "at least one bound"), (4000, 3000, "min_usd 4000 > max_usd 3000")],
)
def test_usd_range_refuses_impossible_bounds(
    min_usd: int | None, max_usd: int | None, error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        UsdRange(min_usd, max_usd)


@pytest.mark.parametrize(
    ("min_usd", "max_usd", "expected"),
    [
        (None, None, None),
        (3000, None, UsdRange(3000, None)),
        (None, 2500, UsdRange(None, 2500)),
        (2000, 4000, UsdRange(2000, 4000)),
    ],
)
def test_usd_range_from_stored(
    min_usd: int | None, max_usd: int | None, expected: UsdRange | None
) -> None:
    assert UsdRange.from_stored(min_usd, max_usd) == expected
