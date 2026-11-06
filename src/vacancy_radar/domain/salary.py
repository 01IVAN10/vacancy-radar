"""Salary extraction from vacancy text. Pure: no I/O.

There are two entry points:

- `parse_salary_phrase` reads a string that is nothing but a salary, such as
  the salary segment of a DOU title (`$900–2200`).
- `find_salary` looks for a salary in free text, such as a Djinni description.

Free text is full of money that is not a salary: ad budgets
(`бюджетами від $100 000 на місяць`), donations (`4 000 000 $`), deal sizes,
referral bonuses. A wrong salary is worse than none, because it misleads both
analytics and subscription matching. So `find_salary` is conservative: an
amount counts only when the nearest keyword before it says it is pay, or,
with no keyword at all, when it names its own period (`$2,500/month`). It
must also be plausible for its currency. Everything else is ignored.

Both functions expect text that went through `domain.text.normalize_text`:
single ASCII spaces, no entities.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

Currency = Literal["USD", "EUR", "UAH"]
Period = Literal["month", "year", "hour"]


@dataclass(frozen=True, slots=True)
class Salary:
    """A salary as the vacancy states it: in its own currency and period.

    An open range has one bound: "від 30 000 грн" has only `min_amount`,
    "up to $2000" only `max_amount`.
    """

    min_amount: int | None
    max_amount: int | None
    currency: Currency
    period: Period = "month"

    def __post_init__(self) -> None:
        if self.min_amount is None and self.max_amount is None:
            raise ValueError("a salary needs at least one bound")
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError(f"min_amount {self.min_amount} > max_amount {self.max_amount}")


# --- the money pattern -------------------------------------------------------

# "2,000", "15 000", "1 500 000" (thousands separators), or "2000", "1.5", "4,5".
NUMBER = r"\d{1,3}(?:[ ,.]\d{3})+(?!\d)|\d+(?:[.,]\d{1,2})?"

MULTIPLIERS: Final[dict[str, int]] = {
    "k": 1_000,
    "к": 1_000,
    "тис": 1_000,
    "тисяч": 1_000,
    "m": 1_000_000,
    "м": 1_000_000,
    "млн": 1_000_000,
    "мільйон": 1_000_000,
    "million": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "млрд": 1_000_000_000,
    "мільярд": 1_000_000_000,
    "billion": 1_000_000_000,
}
# Millions and billions are never salaries, but they must be recognized:
# otherwise "$3 млн" would read as "$3".
MULTIPLIER = (
    r"(?:k|к|тис\.?|тисяч\w*|m|м|млн|мільйон\w*|million|bn|b|млрд|мільярд\w*|billion)(?!\w)"
)

# Each pattern is matched in full against a currency the MONEY pattern found.
CURRENCY_WORDS: Final[tuple[tuple[str, Currency], ...]] = (
    (r"\$|usd|дол\.?|долар\w*|dollars?", "USD"),
    (r"€|eur|євро|euros?", "EUR"),
    (r"₴|uah|грн\.?|грив\w*", "UAH"),
)
# Before the number only symbols and codes: "$2000", "USD 2000".
CURRENCY_BEFORE = r"(?:\$|€|₴|(?<!\w)(?:usd|eur|uah)(?!\w))"
# After the number also words. The lookbehind lets a digit stand right before
# the word ("25000грн") but not a letter.
CURRENCY_AFTER = (
    r"(?:\$|€|₴|(?<![^\W\d_])"
    r"(?:usd|eur|uah|грн\.?|грив\w*|євро|дол\.?|долар\w*|dollars?|euros?)(?!\w))"
)

DASH = r"[-–—−]"

MONEY = re.compile(
    rf"""
    (?<![\w$€₴.,])                                        # not inside a word or number
    (?:(?P<lead>від|from|up\ to|до|starting\ (?:at|from))\ )?
    (?P<cur_a>{CURRENCY_BEFORE})?\ ?
    (?P<low>{NUMBER})\ ?(?P<mult_a>{MULTIPLIER})?
    (?:                                                   # optional upper bound
        \ ?(?P<cur_b>{CURRENCY_AFTER})?
        \ ?(?:{DASH}|(?<=\ )(?:to|до)(?=\ ))\ ?
        (?P<cur_c>{CURRENCY_BEFORE})?\ ?
        (?P<high>{NUMBER})\ ?(?P<mult_b>{MULTIPLIER})?
    )?
    (?P<plus>\ ?\+(?!\ ?[\d$€₴]))?                       # "3000+", not "$1500 + $1500"
    (?:\ ?(?P<cur_d>{CURRENCY_AFTER}))?
    """,
    re.IGNORECASE | re.VERBOSE,
)

PERIOD_WORDS: Final[dict[Period | Literal["day"], tuple[str, ...]]] = {
    "month": ("month", "months", "mo", "monthly", "місяць", "міс", "щомісяця", "щомісячно"),
    "year": ("year", "years", "yr", "annum", "annually", "yearly", "рік", "щороку"),
    "hour": ("hour", "hours", "hr", "hourly", "годину", "год", "щогодини"),
    # Recognized so that "$1000/день" is not read as a monthly salary.
    "day": ("day", "days", "daily", "день", "щодня"),
}
PERIOD_UNITS = {word: period for period, words in PERIOD_WORDS.items() for word in words}
# "/month", "per month", "a month", "на місяць", "щомісяця" right after an amount.
PERIOD_AFTER = re.compile(
    r"\ ?(?:/\ ?|(?:per|a|an|на|в|за)\ )?(?P<unit>{})(?!\w)".format("|".join(PERIOD_UNITS)),
    re.IGNORECASE,
)

# --- context keywords ----------------------------------------------------------

# Stems match the start of a word ("зарплат" matches "зарплата", "зарплатою");
# entries ending in (?!\w) must match the whole word.
PAY_WORDS = re.compile(
    r"""(?<!\w)(?:
        зарплат|заробіт|зп(?!\w)|вилк|оклад|ставк|рейт(?!\w)|дохід|доход|винагород|оплат|
        грошове\ забезпечення|фінансова\ мотивац|
        гроші(?=:)|💰|                                    # labels: "Гроші: 60 000 грн", "💰 Умови"
        salar|compensation|pay(?!\w)|base(?!\w)|income|rate(?!\w)|wage|earn
    )""",
    re.IGNORECASE | re.VERBOSE,
)
NOT_PAY_WORDS = re.compile(
    r"""(?<!\w)(?:
        бюджет|виручк|оборот|угод|чек(?!\w)|інвестиц|залучил|донат|задонат|фонд|
        рекомендац|бонус|премі|kpi|компенсу|компенсац|витрат|економі|спенд|профіт|ціль(?!\w)|
        ринк|ринок|підписк|ціна|ціну|вартіст|кошт|капітал|
        budget|revenue|turnover|deal|invest|raised|donat|fund|aid(?!\w)|referral|bonus|
        commission|wellness|spend|profit|target|goal|market|subscription|price|cost|capital|
        valuation
    )""",
    re.IGNORECASE | re.VERBOSE,
)
# How far before an amount its keyword may stand: "Ми пропонуємо: заробітну плату $1000".
# The window also stops at the start of the amount's sentence: a keyword in
# the sentence before ("…acquisition deals. The role $3,000–5,000 per month")
# is about something else.
CONTEXT_CHARS = 60
SENTENCE_END = re.compile(r"[.!?;] ")

# Plausible monthly amounts. A bound outside them is not a salary: a budget,
# a donation total, a wellness allowance, a referral bonus.
MONTHLY_BOUNDS: Final[dict[Currency, tuple[int, int]]] = {
    "USD": (100, 30_000),
    "EUR": (100, 30_000),
    "UAH": (8_000, 1_500_000),
}
# Only for the plausibility check: the vacancy does not say how many hours it pays for.
HOURS_PER_MONTH = 160


# --- public API ----------------------------------------------------------------


def parse_salary_phrase(text: str) -> Salary | None:
    """Parse a string that consists of one salary and nothing else.

    `"$900–2200"`, `"від $1000"`, `"до 60 000 грн"`, `"$25/hour"`. Anything
    else in the string, or an unsupported period (per day), gives `None`.
    """
    text = text.strip()
    match = MONEY.match(text)
    if match is None:
        return None
    period, end = _period_after(text, match.end())
    if text[end:].strip() or period == "day":
        return None
    return _salary_from(match, period or "month")


def find_salary(text: str) -> Salary | None:
    """The first amount in free text that is clearly a salary, or `None`.

    An amount is a salary when the nearest pay-or-not-pay keyword in the
    `CONTEXT_CHARS` before it, within its sentence, is a pay word (`зарплата`, `ставка`, `salary`),
    or when there is no keyword and the amount names its period (`/month`,
    `на місяць`). It must also fall within `MONTHLY_BOUNDS` for its currency.
    """
    for match in MONEY.finditer(text):
        period, _ = _period_after(text, match.end())
        if period == "day":
            continue
        context = _context(text[max(0, match.start() - CONTEXT_CHARS) : match.start()])
        if context == "not_pay" or (context is None and period is None):
            continue
        salary = _salary_from(match, period or "month")
        if salary is not None and _is_plausible(salary):
            return salary
    return None


# --- helpers -------------------------------------------------------------------


def _salary_from(match: re.Match[str], period: Period) -> Salary | None:
    """Build a `Salary` from a `MONEY` match, or `None` if it is not one."""
    currencies = {
        currency_of(match[group])
        for group in ("cur_a", "cur_b", "cur_c", "cur_d")
        if match[group] is not None
    }
    if len(currencies) != 1:
        return None
    (currency,) = currencies

    low = _number(match["low"])
    mult_a, mult_b = _multiplier(match["mult_a"]), _multiplier(match["mult_b"])
    if match["high"] is None:
        amount = _to_int(low * mult_a)
        lead = (match["lead"] or "").lower()
        if lead in {"up to", "до"}:
            return Salary(None, amount, currency, period)
        if lead or match["plus"]:
            return Salary(amount, None, currency, period)
        return Salary(amount, amount, currency, period)

    high = _number(match["high"])
    # "$5–10K" and "30–40 тис." put the multiplier on the upper bound only.
    if match["mult_a"] is None and match["mult_b"] is not None and low <= high:
        mult_a = mult_b
    min_amount, max_amount = _to_int(low * mult_a), _to_int(high * mult_b)
    if min_amount > max_amount:
        return None
    return Salary(min_amount, max_amount, currency, period)


def currency_of(word: str) -> Currency:
    """Map a currency symbol or word to its code: `"грн"` → `"UAH"`."""
    for pattern, currency in CURRENCY_WORDS:
        if re.fullmatch(pattern, word, re.IGNORECASE):
            return currency
    raise ValueError(f"not a currency: {word!r}")


def _period_after(text: str, pos: int) -> tuple[Period | Literal["day"] | None, int]:
    """The period stated right after an amount, and where its text ends."""
    match = PERIOD_AFTER.match(text, pos)
    if match is None:
        return None, pos
    return PERIOD_UNITS[match["unit"].lower()], match.end()


def _context(before: str) -> Literal["pay", "not_pay"] | None:
    """Which kind of keyword stands closest before an amount in its sentence, if any."""
    boundaries = [m.end() for m in SENTENCE_END.finditer(before)]
    if boundaries:
        before = before[boundaries[-1] :]
    pay = [m.end() for m in PAY_WORDS.finditer(before)]
    not_pay = [m.end() for m in NOT_PAY_WORDS.finditer(before)]
    if not pay and not not_pay:
        return None
    return "pay" if max(pay, default=-1) > max(not_pay, default=-1) else "not_pay"


def _is_plausible(salary: Salary) -> bool:
    low, high = MONTHLY_BOUNDS[salary.currency]
    factor = {"month": Decimal(1), "year": Decimal(1) / 12, "hour": Decimal(HOURS_PER_MONTH)}
    amounts = (a for a in (salary.min_amount, salary.max_amount) if a is not None)
    return all(low <= a * factor[salary.period] <= high for a in amounts)


def _number(text: str) -> Decimal:
    """`"2,000"` → 2000, `"15 000"` → 15000, `"1.5"` → 1.5, `"4,5"` → 4.5."""
    if re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+", text):
        return Decimal(re.sub(r"[ ,.]", "", text))
    return Decimal(text.replace(",", "."))


def _multiplier(word: str | None) -> int:
    if word is None:
        return 1
    word = word.lower().rstrip(".")
    for prefix in ("тисяч", "мільйон", "мільярд"):
        if word.startswith(prefix):
            return MULTIPLIERS[prefix]
    return MULTIPLIERS[word]


def _to_int(amount: Decimal) -> int:
    return int(amount.to_integral_value())
