from dataclasses import replace

import pytest

from vacancy_radar.notify.message import (
    MAX_COMPANY_LENGTH,
    MAX_MESSAGE_LENGTH,
    MAX_SKILLS,
    VacancyCard,
    format_salary,
    message_length,
    render_message,
)

SITE = "https://radar.example"

CARD = VacancyCard(
    title="Senior Python Developer",
    url="https://djinni.co/jobs/1-senior-python/",
    source_name="Djinni",
    company="Acme",
    cities=["Kyiv", "Lviv"],
    salary_min=4000,
    salary_max=5500,
    salary_currency="USD",
    salary_period="month",
    seniority=["senior"],
    work_formats=["remote"],
    skills=["python", "django"],
)


def test_shows_the_fields_and_links_to_the_original() -> None:
    message = render_message(CARD, ["Python remote"], site_url=SITE)

    assert message == (
        "<b>Senior Python Developer</b>\n"
        "Acme, Kyiv, Lviv\n"
        "💰 $4000-5500 a month\n"
        "Senior · Remote\n"
        "Python, Django\n"
        '<a href="https://djinni.co/jobs/1-senior-python/">Open on Djinni</a>\n'
        "\n"
        "<i>Matched: Python remote</i> · "
        '<a href="https://radar.example/subscriptions">Manage subscriptions</a>'
    )


def test_leaves_out_what_is_unknown() -> None:
    bare = replace(
        CARD,
        company=None,
        cities=[],
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_period=None,
        seniority=[],
        work_formats=[],
        skills=[],
    )

    lines = render_message(bare, ["Any"], site_url=SITE).split("\n")

    assert lines[:2] == [
        "<b>Senior Python Developer</b>",
        '<a href="https://djinni.co/jobs/1-senior-python/">Open on Djinni</a>',
    ]


def test_escapes_every_value() -> None:
    hostile = replace(
        CARD,
        title="C++ <b>&</b> Rust",
        company="<script>",
        url='https://djinni.co/jobs/1/?a=1&b="2"',
    )

    message = render_message(hostile, ["<mine> & yours"], site_url=SITE)

    assert "<b>C++ &lt;b&gt;&amp;&lt;/b&gt; Rust</b>" in message
    assert "&lt;script&gt;" in message
    assert 'href="https://djinni.co/jobs/1/?a=1&amp;b=&quot;2&quot;"' in message
    assert "Matched: &lt;mine&gt; &amp; yours" in message
    assert "<script>" not in message


def test_lists_a_few_skills_and_counts_the_rest() -> None:
    many = replace(
        CARD,
        skills=[
            "python",
            "django",
            "fastapi",
            "flask",
            "postgresql",
            "redis",
            "docker",
            "kubernetes",
            "aws",
            "terraform",
        ],
    )

    skills_line = render_message(many, ["Any"], site_url=SITE).split("\n")[4]

    assert skills_line.count(",") == MAX_SKILLS - 1
    assert skills_line.endswith(" and 2 more")


def test_skips_skills_no_longer_in_the_dictionary() -> None:
    message = render_message(
        replace(CARD, skills=["python", "removed-skill"]), ["Any"], site_url=SITE
    )

    assert "Python\n" in message
    assert "removed-skill" not in message


def test_shortens_a_very_long_company_name() -> None:
    message = render_message(replace(CARD, company="A" * 500), ["Any"], site_url=SITE)

    assert "A" * MAX_COMPANY_LENGTH not in message
    assert "A" * (MAX_COMPANY_LENGTH - 1) + "…" in message


def test_counts_length_as_telegram_does() -> None:
    assert message_length("abc") == 3
    # Outside the Basic Multilingual Plane: two UTF-16 code units.
    assert message_length("🚀") == 2


def test_fits_telegrams_limit_with_names_made_of_emoji() -> None:
    names = ["🚀" * 80 for _ in range(30)]

    message = render_message(CARD, names, site_url=SITE)

    assert message_length(message) <= MAX_MESSAGE_LENGTH
    assert message.endswith("Manage subscriptions</a>")


def test_fits_telegrams_limit_with_many_long_subscription_names() -> None:
    names = [f"Subscription number {n} & " + "x" * 80 for n in range(60)]

    message = render_message(CARD, names, site_url=SITE)

    assert len(message) <= MAX_MESSAGE_LENGTH
    assert message.endswith("Manage subscriptions</a>")
    assert "…</i>" in message


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
