from datetime import UTC, date, datetime

import pytest

from vacancy_radar.domain.analytics import (
    MIN_SAMPLE,
    AnalyticsForm,
    AnalyticsQuery,
    Coverage,
    InvalidAnalyticsError,
    Quartiles,
    SalaryWeek,
    build_analytics_query,
    month_param,
    salary_week,
    share,
)

MONDAY = date(2026, 11, 9)
QUARTILES = Quartiles(p25=1000, median=1500, p75=2200)
COVERAGE = Coverage(first_day=date(2026, 11, 5), today=date(2026, 11, 20))


def test_a_median_of_a_small_sample_is_hidden_but_its_size_is_kept() -> None:
    small = salary_week(MONDAY, MIN_SAMPLE - 1, QUARTILES, COVERAGE)
    enough = salary_week(MONDAY, MIN_SAMPLE, QUARTILES, COVERAGE)

    assert small == SalaryWeek(MONDAY, MIN_SAMPLE - 1, None, partial=False, settling=True)
    assert enough == SalaryWeek(MONDAY, MIN_SAMPLE, QUARTILES, partial=False, settling=True)


@pytest.mark.parametrize(
    ("first_day", "today", "partial"),
    [
        (date(2026, 11, 5), date(2026, 11, 16), False),  # next Monday: the week is over
        (date(2026, 11, 5), date(2026, 11, 15), True),  # Sunday: still being collected
        (date(2026, 11, 5), date(2026, 11, 9), True),  # its Monday
        (date(2026, 11, 9), date(2026, 11, 20), False),  # collection started on its Monday
        (date(2026, 11, 10), date(2026, 11, 20), True),  # and on its Tuesday
    ],
)
def test_week_is_partial(first_day: date, today: date, partial: bool) -> None:
    assert Coverage(first_day, today).week_is_partial(MONDAY) is partial


@pytest.mark.parametrize(
    ("month", "today", "partial"),
    [
        (date(2026, 11, 1), date(2026, 12, 3), True),  # collection started on the 5th
        (date(2026, 12, 1), date(2027, 1, 1), False),  # the next month began
        (date(2026, 12, 1), date(2026, 12, 31), True),  # its last day: still being collected
        (date(2027, 2, 1), date(2027, 2, 28), True),
        (date(2027, 2, 1), date(2027, 3, 1), False),
    ],
)
def test_month_is_partial(month: date, today: date, partial: bool) -> None:
    assert Coverage(date(2026, 11, 5), today).month_is_partial(month) is partial


@pytest.mark.parametrize(
    ("today", "settling"),
    [
        (date(2026, 11, 16), True),  # the week is over, but its twins may still join
        (date(2026, 12, 15), True),  # 29 days after it ended
        (date(2026, 12, 16), False),  # 30 days after: no posting can join it any more
    ],
)
def test_a_week_settles_a_candidate_window_after_it_ends(today: date, settling: bool) -> None:
    assert Coverage(date(2026, 11, 5), today).week_is_settling(MONDAY) is settling


@pytest.mark.parametrize(
    ("today", "settling"),
    [(date(2026, 12, 1), True), (date(2026, 12, 30), True), (date(2026, 12, 31), False)],
)
def test_a_month_settles_a_candidate_window_after_it_ends(today: date, settling: bool) -> None:
    assert Coverage(date(2026, 11, 5), today).month_is_settling(date(2026, 11, 1)) is settling


def test_today_is_the_kyiv_day() -> None:
    # 22:30 UTC on Sunday is 00:30 on Monday in Kyiv: the week of 9 November is over.
    coverage = Coverage.at(date(2026, 11, 5), datetime(2026, 11, 15, 22, 30, tzinfo=UTC))

    assert coverage.today == date(2026, 11, 16)
    assert not coverage.week_is_partial(MONDAY)


@pytest.mark.parametrize(
    ("n", "total", "percent"), [(612, 2428, 25.2), (1, 3, 33.3), (5, 5, 100.0), (0, 0, 0.0)]
)
def test_share(n: int, total: int, percent: float) -> None:
    assert share(n, total) == percent


def test_an_empty_form_asks_for_the_newest_month_and_every_level() -> None:
    assert build_analytics_query(AnalyticsForm()) == AnalyticsQuery(month=None, level=None)


def test_a_month_and_a_level() -> None:
    query = build_analytics_query(AnalyticsForm(month="2026-10", level="senior"))

    assert query == AnalyticsQuery(month=date(2026, 10, 1), level="senior")


def test_a_month_reads_back_as_written() -> None:
    assert month_param(date(2026, 3, 1)) == "2026-03"
    assert build_analytics_query(AnalyticsForm(month="2026-03")).month == date(2026, 3, 1)


@pytest.mark.parametrize(
    "month", ["2026-13", "2026-00", "2026-1", "26-11", "2026-11-01", "1999-12", "２０２６-11"]
)
def test_an_invalid_month_is_refused(month: str) -> None:
    with pytest.raises(InvalidAnalyticsError) as error:
        build_analytics_query(AnalyticsForm(month=month))

    assert error.value.errors == {"month": "Choose a month such as 2026-11."}


@pytest.mark.parametrize("level", ["all", "Senior", "architect"])
def test_an_unknown_level_is_refused(level: str) -> None:
    # "all" is the views' word for every level; the page sends an empty level instead.
    with pytest.raises(InvalidAnalyticsError) as error:
        build_analytics_query(AnalyticsForm(level=level))

    assert error.value.errors == {"level": "Choose one of the offered levels."}


def test_every_invalid_choice_is_reported_at_once() -> None:
    with pytest.raises(InvalidAnalyticsError) as error:
        build_analytics_query(AnalyticsForm(month="x", level="y"))

    assert set(error.value.errors) == {"month", "level"}
