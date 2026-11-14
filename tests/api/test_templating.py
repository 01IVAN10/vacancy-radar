from datetime import date

import pytest

from vacancy_radar.api.templating import day_label, month_label, templates, thousands


@pytest.mark.parametrize(
    ("day", "label"),
    [(date(2026, 11, 9), "9 Nov 2026"), (date(2027, 9, 30), "30 Sep 2027")],
)
def test_a_day(day: date, label: str) -> None:
    assert day_label(day) == label


def test_a_day_without_its_year() -> None:
    assert day_label(date(2026, 1, 5), year=False) == "5 Jan"


def test_a_month() -> None:
    assert month_label(date(2026, 3, 1)) == "March 2026"


def test_thousands() -> None:
    assert [thousands(n) for n in (7, 4046, 1_202_300)] == ["7", "4,046", "1,202,300"]


def test_templates_have_the_filters() -> None:
    rendered = templates.env.from_string(
        "{{ day | day }} · {{ day | month }} · {{ n | thousands }}"
    ).render(day=date(2026, 11, 1), n=4046)

    assert rendered == "1 Nov 2026 · November 2026 · 4,046"
