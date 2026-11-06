"""Salary parsing, tested on phrases from collected vacancies.

Most cases are short fragments of real Djinni and DOU descriptions (collected
2026-11-05/06), cut to the sentence around the amount. Cases marked
"synthetic" cover grammar the collected data has not shown yet.
"""

import pytest

from tests.support import fixture_bytes
from vacancy_radar.domain.salary import (
    Period,
    Salary,
    currency_of,
    find_salary,
    parse_salary_phrase,
)
from vacancy_radar.domain.text import html_to_text
from vacancy_radar.sources import djinni, dou


def usd(low: int | None, high: int | None, period: Period = "month") -> Salary:
    return Salary(low, high, "USD", period)


def uah(low: int | None, high: int | None) -> Salary:
    return Salary(low, high, "UAH")


# --- find_salary: amounts that are salaries ----------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "фіксована ставка 40 000 - 60 000 грн (за результатами співбесіди)",
            uah(40_000, 60_000),
            id="uah-range-spaces",
        ),
        pytest.param(
            "Ми пропонуємо: заробітну плату $1000–1200 (ставка + бонуси)",
            usd(1000, 1200),
            id="usd-range-en-dash",
        ),
        pytest.param(
            "Salary: $2,000–$3,000 USD per month (depending on experience)",
            usd(2000, 3000),
            id="usd-comma-thousands-both-symbols",
        ),
        pytest.param(
            "Compensation: $600–1100/month , depending on your technical level",
            usd(600, 1100),
            id="slash-month",
        ),
        pytest.param("Rate: Up to $25/hour", usd(None, 25, "hour"), id="hourly-up-to"),
        pytest.param(
            "Opportunity to earn up to $60,000 per year in total compensation.",
            usd(None, 60_000, "year"),
            id="yearly",
        ),
        pytest.param(
            "Наша вилка: 30 000 – 91 500 грн, (ставка + щомісячний бонус)",
            uah(30_000, 91_500),
            id="vylka",
        ),
        pytest.param(
            "На цій позиції ми пропонуємо заробітну вилку від 55 000 до 200 000 грн.",
            uah(55_000, 200_000),
            id="vid-do",
        ),
        pytest.param(
            "Заробітна плата: 15 000−20 000 грн (залежить від досвіду)",
            uah(15_000, 20_000),
            id="minus-sign-dash",
        ),
        pytest.param(
            "Зарплата: від 30 000 грн Офіційне працевлаштування.",
            uah(30_000, None),
            id="from-only",
        ),
        pytest.param(
            "Salary: Up to $2000 + KPI-based bonuses", usd(None, 2000), id="up-to-plus-bonus"
        ),
        pytest.param("ЗП $1100−1700 на місяць", usd(1100, 1700), id="zp-na-misyats"),
        pytest.param(
            "Monthly Base Salary: First Month: 750 EUR Third Month: raise of 100-150 EUR",
            Salary(750, 750, "EUR"),
            id="eur-first-amount-wins",
        ),
        pytest.param(
            "Зарплата: ставка $500 та KPI від $500", usd(500, 500), id="first-amount-wins"
        ),
        pytest.param(
            "Fixed base salary ($500—$1,000/month);", usd(500, 1000), id="em-dash-in-parens"
        ),
        pytest.param(
            "Ставка $3000+ на місяць залежно від результатів продажів",
            usd(3000, None),
            id="plus-means-from",
        ),
        pytest.param(
            "фіксовану ставку 30–40 тис. грн залежно від досвіду",
            uah(30_000, 40_000),
            id="tys-multiplier-on-both",
        ),
        pytest.param(
            "оплату — ставку 1500$ (обговорюється на співбесіді)",
            usd(1500, 1500),
            id="suffix-dollar",
        ),
        pytest.param("Щомісячна оплата: 800 доларів США.", usd(800, 800), id="dolariv"),
        pytest.param(
            "базове грошове забезпечення від 25 000 до 127 000 грн залежно від завдань",
            uah(25_000, 127_000),
            id="groshove-zabezpechennia",
        ),
        pytest.param("Дохід $2000–2500 (ставка $1000–1500+%).", usd(2000, 2500), id="dokhid"),
        # No keyword, but the amount names its period.
        pytest.param(
            "Schedule: 3 PM – 12 AM Kyiv time. $2,500/month during the 3-month probation period.",
            usd(2500, 2500),
            id="period-without-keyword",
        ),
        # The nearest keyword decides: "compensation" stands after "Target".
        pytest.param(
            "Target total compensation: Up to $60,000/year",
            usd(None, 60_000, "year"),
            id="nearest-keyword-wins",
        ),
        # A not-pay word in the sentence before does not count.
        pytest.param(
            "into acquisition deals. The role $3,000–5,000 per month.",
            usd(3000, 5000),
            id="not-pay-word-in-sentence-before",
        ),
        pytest.param(
            "Фінансова мотивація — від $700-800 на адаптаційний період, далі $900-950.",
            usd(700, 800),
            id="finansova-motyvatsiia",
        ),
        pytest.param(
            "💰 Умови 35 000 грн — перший місяць 40 000 грн — після адаптації (ставка)",
            uah(35_000, 35_000),
            id="money-bag-label",
        ),
        pytest.param(
            "Що ми пропонуємо? Гроші: 60 000 — 90 000 грн (складається зі ставка + бонуси)",
            uah(60_000, 90_000),
            id="hroshi-label",
        ),
        pytest.param("Зарплата 25000грн", uah(25_000, 25_000), id="synthetic-no-space-hryvnia"),
        pytest.param("Salary: $5–10K", usd(5000, 10_000), id="synthetic-k-on-upper-bound"),
        pytest.param("Salary from $1.5k", usd(1500, None), id="synthetic-decimal-k"),
        pytest.param(
            "Зарплата від 30 тисяч гривень", uah(30_000, None), id="synthetic-tysiach-word"
        ),
        pytest.param(
            "Salary: €3 000 – €4 000 gross", Salary(3000, 4000, "EUR"), id="synthetic-euro-prefix"
        ),
        pytest.param("З/п від 30 000 грн", uah(30_000, None), id="synthetic-z-slash-p"),
        # "курсом" is the exchange rate, not a course: it must not rule the amount out.
        pytest.param(
            "Зарплата за курсом НБУ: $1500", usd(1500, 1500), id="synthetic-exchange-rate"
        ),
    ],
)
def test_finds_salary(text: str, expected: Salary) -> None:
    assert find_salary(text) == expected


# --- find_salary: amounts that are not salaries --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "Досвід роботи з рекламними бюджетами понад $10,000/місяць в межах 1-го проєкту",
            id="budget-per-month",
        ),
        pytest.param("які працюють із бюджетами від $100K на місяць.", id="budget-100k"),
        pytest.param(
            "ми виділили понад 4 000 000 $ на мілітарний та гуманітарний напрямок.",
            id="donation-total",
        ),
        pytest.param("Робоча зв'язка отримує від $1000/день", id="per-day"),
        pytest.param("Experience selling B2B services with a $5–10K deal size.", id="deal-size"),
        pytest.param("Deal sizes range from $3,000 to $500,000.", id="deal-range"),
        pytest.param("Компанія вже залучила $3 млн інвестицій.", id="investment-millions"),
        pytest.param("Revenue $2М+ , з портфелем брендів", id="revenue-cyrillic-m"),
        pytest.param(
            "Оплата: стартап залучив 2 мільйони доларів", id="synthetic-inflected-millions"
        ),
        pytest.param("За успішну рекомендацію ми виплачуємо $500.", id="referral"),
        pytest.param("Щороку компенсуємо до $2000 на wellness або спорт", id="wellness-yearly"),
        pytest.param(
            "ти отримаєш бонус $1500 + $1500 ми задонатимо у фонд підтримки ЗСУ",
            id="referral-bonus",
        ),
        pytest.param("Monthly wellness allowance (~$50) for mental health", id="allowance"),
        pytest.param(
            "Compensation Base + monthly performance bonus: $1K for hitting both targets",
            id="bonus-nearer-than-compensation",
        ),
        pytest.param("Ціль — 50 000 $ на місяць за пів року", id="target"),
        pytest.param("Ролик, що раніше коштував 5 000 $, у нас коштує близько 100 $.", id="cost"),
        pytest.param("що дозволяє зменшити особисті витрати до 5 000 грн щомісяця.", id="expenses"),
        pytest.param("Free Plan та платні підписки (Basic $19 / Plus $79).", id="prices"),
        pytest.param("Конкурентна зарплата та бонуси.", id="no-amount"),
        pytest.param("Оплату в USD", id="currency-without-amount"),
        pytest.param("Зарплата 3000 після випробувального терміну", id="amount-without-currency"),
        # Pay words count only in the amount's own sentence.
        pytest.param(
            "Хороша зарплата. Щорічно $500 на навчання.", id="synthetic-pay-word-sentence-before"
        ),
        pytest.param("Без «затискання» грошей $1000", id="synthetic-hroshi-not-a-label"),
        # Benefits with an amount and a period: an allowance, not the salary.
        pytest.param(
            "Benefits: medical insurance compensation up to $150 per month, paid vacation.",
            id="synthetic-insurance-compensation",
        ),
        pytest.param(
            "We offer: compensation of English courses up to $200/month.",
            id="synthetic-courses-compensation",
        ),
        pytest.param("Оплата обіду 200 $ на місяць", id="synthetic-lunch-payment"),
        # Conservative misses: a real salary, but too little evidence for it.
        pytest.param("Hourly rate up to $25 USD.", id="miss-period-before-amount"),
        pytest.param("Зарплата: $1000 – 1200 €", id="mixed-currencies"),
    ],
)
def test_ignores_amounts_that_are_not_clearly_salary(text: str) -> None:
    assert find_salary(text) is None


# --- find_salary on whole collected descriptions ---------------------------------


def descriptions(fixture: str) -> dict[str, str]:
    parse = djinni.parse_feed if fixture.startswith("djinni/") else dou.parse_feed
    items = parse(fixture_bytes(fixture)).items
    return {item.external_id: html_to_text(item.payload["description"]) for item in items}


FIXTURE_SALARIES = {
    "djinni/rss_all.xml": {
        "852239": uah(40_000, 60_000),
        "856858": None,  # a donation total, not a salary
        "856870": None,
        "856869": None,
        "850004": usd(1000, 1200),
    },
    "djinni/rss_python.xml": {
        "856775": None,
        "856641": usd(None, 25, "hour"),
        "856036": usd(600, 1100),
        "832898": None,
        "852433": usd(2000, 3000),
    },
    # DOU states salaries in the title, which is parsed separately.
    "dou/rss_all.xml": dict.fromkeys(
        ["379095", "379092", "379090", "374387", "379075", "379053"], None
    ),
}


@pytest.mark.parametrize("fixture", sorted(FIXTURE_SALARIES))
def test_finds_salaries_in_fixture_descriptions(fixture: str) -> None:
    found = {ext_id: find_salary(text) for ext_id, text in descriptions(fixture).items()}

    assert found == FIXTURE_SALARIES[fixture]


# --- parse_salary_phrase ---------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        # Salary segments of real DOU titles.
        pytest.param("$900–2200", usd(900, 2200), id="dou-range"),
        pytest.param("$4500–5000", usd(4500, 5000), id="dou-range-2"),
        pytest.param("$400–7000", usd(400, 7000), id="dou-wide-range"),
        pytest.param("від $1000", usd(1000, None), id="from"),
        pytest.param("до $3000", usd(None, 3000), id="up-to"),
        pytest.param("$3000", usd(3000, 3000), id="single"),
        pytest.param("30–40 тис. грн", uah(30_000, 40_000), id="tys"),
        pytest.param("$25/hour", usd(25, 25, "hour"), id="hourly"),
        pytest.param(" $900–2200 ", usd(900, 2200), id="surrounding-spaces"),
        # Plausibility is not checked: a structured field is taken as stated.
        pytest.param("$50", usd(50, 50), id="small-amount"),
    ],
)
def test_parses_salary_phrase(phrase: str, expected: Salary) -> None:
    assert parse_salary_phrase(phrase) == expected


@pytest.mark.parametrize(
    "phrase",
    [
        pytest.param("Львів", id="city"),
        pytest.param("віддалено", id="remote-flag"),
        pytest.param("$900–2200, Львів", id="trailing-text"),
        pytest.param("2200–900 $", id="min-above-max"),
        pytest.param("$1000/день", id="per-day"),
        pytest.param("1000", id="no-currency"),
        pytest.param("$1000–€1200", id="mixed-currencies"),
        pytest.param("", id="empty"),
    ],
)
def test_rejects_phrases_that_are_not_one_salary(phrase: str) -> None:
    assert parse_salary_phrase(phrase) is None


# --- Salary and helpers ---------------------------------------------------------------


def test_salary_needs_a_bound() -> None:
    with pytest.raises(ValueError, match="at least one bound"):
        Salary(None, None, "USD")


def test_salary_bounds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="min_amount 2000 > max_amount 1000"):
        Salary(2000, 1000, "USD")


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("$", "USD"),
        ("доларів", "USD"),
        ("€", "EUR"),
        ("євро", "EUR"),
        ("грн.", "UAH"),
        ("₴", "UAH"),
    ],
)
def test_currency_of(word: str, expected: str) -> None:
    assert currency_of(word) == expected


def test_currency_of_rejects_unknown_words() -> None:
    with pytest.raises(ValueError, match="not a currency"):
        currency_of("zł")
