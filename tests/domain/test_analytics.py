from datetime import date

import pytest

from vacancy_radar.domain.analytics import (
    MIN_SAMPLE,
    Quartiles,
    SalaryWeek,
    month_is_partial,
    salary_week,
    share,
    week_is_partial,
)

MONDAY = date(2026, 11, 9)
QUARTILES = Quartiles(p25=1000, median=1500, p75=2200)


def test_a_median_of_a_small_sample_is_hidden_but_its_size_is_kept() -> None:
    small = salary_week(
        MONDAY, MIN_SAMPLE - 1, QUARTILES, first_day=date(2026, 11, 5), today=date(2026, 11, 20)
    )
    enough = salary_week(
        MONDAY, MIN_SAMPLE, QUARTILES, first_day=date(2026, 11, 5), today=date(2026, 11, 20)
    )

    assert small == SalaryWeek(MONDAY, MIN_SAMPLE - 1, None, partial=False)
    assert enough == SalaryWeek(MONDAY, MIN_SAMPLE, QUARTILES, partial=False)


@pytest.mark.parametrize(
    ("first_day", "today", "partial"),
    [
        (date(2026, 11, 5), date(2026, 11, 15), False),  # Sunday: the week is over
        (date(2026, 11, 5), date(2026, 11, 14), True),  # Saturday: not yet
        (date(2026, 11, 9), date(2026, 11, 20), False),  # collection started on its Monday
        (date(2026, 11, 10), date(2026, 11, 20), True),  # and on its Tuesday
    ],
)
def test_week_is_partial(first_day: date, today: date, partial: bool) -> None:
    assert week_is_partial(MONDAY, first_day=first_day, today=today) is partial


@pytest.mark.parametrize(
    ("month", "first_day", "today", "partial"),
    [
        (date(2026, 11, 1), date(2026, 11, 5), date(2026, 12, 3), True),  # started on the 5th
        (date(2026, 12, 1), date(2026, 11, 5), date(2026, 12, 31), False),
        (date(2026, 12, 1), date(2026, 11, 5), date(2026, 12, 30), True),
        (date(2027, 2, 1), date(2026, 11, 5), date(2027, 2, 28), False),
    ],
)
def test_month_is_partial(month: date, first_day: date, today: date, partial: bool) -> None:
    assert month_is_partial(month, first_day=first_day, today=today) is partial


@pytest.mark.parametrize(
    ("n", "total", "percent"), [(612, 2428, 25.2), (1, 3, 33.3), (5, 5, 100.0), (0, 0, 0.0)]
)
def test_share(n: int, total: int, percent: float) -> None:
    assert share(n, total) == percent
