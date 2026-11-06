"""Converting a salary to US dollars per month. Pure: no I/O.

Subscriptions filter on a minimum salary in USD per month, and analytics
compares salaries across currencies, so every salary also gets a monthly USD
range. It is computed with the official NBU rates of the day the vacancy was
published. A yearly salary is divided by 12. An hourly one gets no monthly
value: the vacancy does not say how many hours it pays for.
"""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from vacancy_radar.domain.salary import Salary

# Currencies NBU rates are kept for, in hryvnias per unit.
RateCurrency = Literal["USD", "EUR"]
RATE_CURRENCIES: Final[tuple[RateCurrency, ...]] = ("USD", "EUR")

MONTHS_PER_YEAR = 12


def to_monthly_usd(
    salary: Salary, rates: Mapping[RateCurrency, Decimal] | None
) -> tuple[int | None, int | None]:
    """The salary's bounds in whole US dollars per month.

    `rates` are hryvnias per unit of each currency on the vacancy's day. Both
    bounds are `None` for an hourly salary, and when a rate it needs is
    missing. An open bound stays `None`.
    """
    if salary.period == "hour":
        return None, None
    usd_per_unit = _usd_per_unit(salary, rates or {})
    if usd_per_unit is None:
        return None, None

    per_month = Decimal(1) if salary.period == "month" else Decimal(1) / MONTHS_PER_YEAR
    factor = usd_per_unit * per_month
    return _convert(salary.min_amount, factor), _convert(salary.max_amount, factor)


def _usd_per_unit(salary: Salary, rates: Mapping[RateCurrency, Decimal]) -> Decimal | None:
    """How many dollars one unit of the salary's currency is worth, via hryvnias."""
    if salary.currency == "USD":
        return Decimal(1)
    uah_per_usd = rates.get("USD")
    if uah_per_usd is None:
        return None
    if salary.currency == "UAH":
        return 1 / uah_per_usd
    uah_per_eur = rates.get("EUR")
    return None if uah_per_eur is None else uah_per_eur / uah_per_usd


def _convert(amount: int | None, factor: Decimal) -> int | None:
    if amount is None:
        return None
    return int((amount * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
