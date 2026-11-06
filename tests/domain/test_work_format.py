"""Work-format detection, tested on phrases from collected vacancies (2026-11-05/06)."""

import pytest

from vacancy_radar.domain.work_format import WorkFormat, detect_work_formats


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        pytest.param("Senior Quant Developer (Python), Warsaw, hybrid", ("hybrid",), id="hybrid"),
        pytest.param("Media Buyer (Remote)", ("remote",), id="remote"),
        pytest.param("RF Engineer | Kyiv, Lviv | Office-based", ("office",), id="office-based"),
        pytest.param("Office Manager", (), id="office-manager-is-a-role"),
        pytest.param("Back Office Specialist", (), id="back-office"),
    ],
)
def test_reads_formats_from_the_title(title: str, expected: tuple[WorkFormat, ...]) -> None:
    assert detect_work_formats(title, "") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("full remote work flexible schedule", ("remote",), id="full-remote"),
        pytest.param("Location: Remote Employment type: Full-time", ("remote",), id="location"),
        pytest.param(
            "Work arrangement: remote • Schedule: full-time", ("remote",), id="arrangement"
        ),
        pytest.param("remote-first culture", ("remote",), id="remote-first"),
        pytest.param("ability to work fully remotely", ("remote",), id="remotely"),
        pytest.param(
            "Що ми пропонуємо: • повністю віддалений формат роботи.", ("remote",), id="ua"
        ),
        pytest.param("Умови: • дистанційна робота.", ("remote",), id="dystantsiina"),
        pytest.param("формат роботи — remote", ("remote",), id="ua-label-en-word"),
        pytest.param(
            "we provide a flexible hybrid work model as part of our culture",
            ("hybrid",),
            id="hybrid-work",
        ),
        pytest.param("Формат роботи: гібридний формат, Київ (Поділ).", ("hybrid",), id="hybrid-ua"),
        pytest.param("Гібрид у Львові - офіс два дні на тиждень", ("hybrid",), id="hybrid-in-city"),
        pytest.param("стандартно працюємо в офісі з 9:00 до 18:00", ("office",), id="office-ua"),
        pytest.param("Місце та формат роботи: офіс у Києві.", ("office",), id="office-label"),
        pytest.param(
            "This is an on-site role based in our modern office", ("office",), id="on-site"
        ),
        # Several formats offered.
        pytest.param(
            "можливість працювати повністю віддалено або в офісі (Одеса, Київ)",
            ("remote",),
            id="remote-or-office-without-work-verb",
        ),
        pytest.param(
            "Flexible working format - remote, office-based or flexible",
            ("remote", "office"),
            id="remote-and-office",
        ),
        pytest.param(
            "графік: пн -пт 9:00-18:00 (офіс, віддалено, гібридний графік)",
            ("hybrid",),
            id="bare-words-in-a-list-are-not-enough",
        ),
        # Not a work format.
        pytest.param(
            "Ability to work from the Warsaw office most days (fully remote is not possible)",
            (),
            id="negated-remote",
        ),
        pytest.param("Віддалена робота неможлива.", (), id="negated-remote-ua"),
        pytest.param("Remote work: not available", (), id="negated-label"),
        pytest.param("experience with hybrid cloud deployments", (), id="hybrid-cloud"),
        pytest.param("home office budget. laptop or", (), id="home-office"),
        pytest.param("Microsoft Office, антивірус", (), id="microsoft-office"),
        pytest.param("до 10 днів віддаленої роботи на рік", (), id="wfh-days-in-office-job"),
        pytest.param(
            "робота з періодичною можливістю працювати дистанційно", (), id="occasional-wfh"
        ),
        pytest.param("послуга «віддалений рекрутер»", (), id="remote-recruiter-service"),
        pytest.param("", (), id="empty"),
    ],
)
def test_reads_explicit_phrases_from_the_description(
    text: str, expected: tuple[WorkFormat, ...]
) -> None:
    assert detect_work_formats("", text) == expected


def test_adds_formats_flagged_by_the_source() -> None:
    assert detect_work_formats("Data Engineer", "", flagged=("remote",)) == ("remote",)


def test_returns_formats_in_a_fixed_order() -> None:
    text = "Office-based, hybrid work or fully remote"

    assert detect_work_formats("", text, flagged=("office",)) == ("remote", "hybrid", "office")
