"""DOU title parsing, tested on titles of collected vacancies (2026-11-05/06)."""

import pytest

from tests.support import fixture_bytes
from vacancy_radar.domain.dou_title import DouTitle, parse_dou_title
from vacancy_radar.domain.salary import Salary
from vacancy_radar.domain.text import normalize_text
from vacancy_radar.sources import dou


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        pytest.param(
            "Інженер-пілот БпЛА (R&D) в Orients, $900–2200, Львів",
            DouTitle(
                position="Інженер-пілот БпЛА (R&D)",
                company="Orients",
                salary=Salary(900, 2200, "USD"),
                cities=("Львів",),
            ),
            id="salary-and-city",
        ),
        pytest.param(
            "Product Manager в SQUAD, Київ, Львів, за кордоном, віддалено",
            DouTitle(
                position="Product Manager",
                company="SQUAD",
                cities=("Київ", "Львів"),
                abroad=True,
                work_formats=("remote",),
            ),
            id="cities-abroad-remote",
        ),
        pytest.param(
            "Marketing Analyst в Genesis, Київ, гібрид",
            DouTitle(
                position="Marketing Analyst",
                company="Genesis",
                cities=("Київ",),
                work_formats=("hybrid",),
            ),
            id="hybrid-flag",
        ),
        pytest.param(
            "Спеціаліст з кадрового обліку в MilTech-компанію (NDA) в NDA (Miltech), Київ",
            DouTitle(
                position="Спеціаліст з кадрового обліку в MilTech-компанію (NDA)",
                company="NDA (Miltech)",
                cities=("Київ",),
            ),
            id="split-at-the-last-v",
        ),
        pytest.param(
            "Senior Full-Stack Developer (TypeScript / Python, AWS) в Codebridge Technology, "
            "Inc., Київ, віддалено",
            DouTitle(
                position="Senior Full-Stack Developer (TypeScript / Python, AWS)",
                company="Codebridge Technology, Inc.",
                cities=("Київ",),
                work_formats=("remote",),
            ),
            id="legal-suffix-stays-with-company",
        ),
        pytest.param(
            "Фахівець з обробки баз даних в Азов, 1 корпус НГУ, $600–2800",
            DouTitle(
                position="Фахівець з обробки баз даних",
                company="Азов, 1 корпус НГУ",
                salary=Salary(600, 2800, "USD"),
            ),
            id="digit-stays-with-company-salary-does-not",
        ),
        pytest.param(
            "Senior Backend Engineer ID99040 в AgileEngine, Poland (remote), Ukraine (remote), "
            "Львів",
            DouTitle(
                position="Senior Backend Engineer ID99040",
                company="AgileEngine",
                cities=("Poland", "Ukraine", "Львів"),
                work_formats=("remote",),
            ),
            id="remote-places",
        ),
        pytest.param(
            "Фінансовий контролер в Джоб Хант, від $2200, Київ",
            DouTitle(
                position="Фінансовий контролер",
                company="Джоб Хант",
                salary=Salary(2200, None, "USD"),
                cities=("Київ",),
            ),
            id="salary-from",
        ),
        pytest.param(
            "Бухгалтер в NDA Drone Development, до $1300, Київ (правий беріг)",
            DouTitle(
                position="Бухгалтер",
                company="NDA Drone Development",
                salary=Salary(None, 1300, "USD"),
                cities=("Київ (правий беріг)",),
            ),
            id="salary-up-to",
        ),
        pytest.param(
            "Оператор РЕБ в Окремий спеціальний центр електронної підтримки",
            DouTitle(
                position="Оператор РЕБ", company="Окремий спеціальний центр електронної підтримки"
            ),
            id="company-only",
        ),
        pytest.param(
            "QA Engineer в Acme, , Київ",
            DouTitle(position="QA Engineer", company="Acme", cities=("Київ",)),
            id="synthetic-empty-segment",
        ),
        pytest.param(
            "Position without a company",
            DouTitle(position="Position without a company", company=None),
            id="no-separator",
        ),
    ],
)
def test_parse_dou_title(title: str, expected: DouTitle) -> None:
    assert parse_dou_title(title) == expected


def test_parses_every_fixture_title() -> None:
    items = dou.parse_feed(fixture_bytes("dou/rss_all.xml")).items
    parsed = [parse_dou_title(normalize_text(item.payload["title"])) for item in items]

    assert [(t.company, t.salary, t.work_formats) for t in parsed] == [
        ("СoinPayments", None, ("remote",)),
        ("HOLYWATER TECH", None, ("remote",)),
        ("Orients", Salary(900, 2200, "USD"), ()),
        ("Innovation Group", None, ("remote",)),
        ("SoftServe", None, ("remote",)),
        ("NextDev IT Teams", Salary(4500, 5000, "USD"), ("remote",)),
    ]
    # The bold title is plain text after normalization.
    assert parsed[5].position == "Senior DevOps Engineer (Azure / AKS)"
    assert len(parsed[4].cities) == 12
