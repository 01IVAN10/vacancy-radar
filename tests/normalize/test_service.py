"""Reprocessing against real Postgres, with NBU mocked by respx."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from itertools import product
from typing import get_args

import pytest
import respx
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from tests.notify.factories import add_vacancy
from tests.support import fixture_bytes
from vacancy_radar.collect.service import ensure_source, upsert_raw_items
from vacancy_radar.db.models import RawVacancy, Skill, Source, Vacancy
from vacancy_radar.domain.money import RateCurrency, to_monthly_usd
from vacancy_radar.domain.normalize import NORMALIZER_VERSION
from vacancy_radar.domain.salary import Currency, Period, Salary
from vacancy_radar.domain.skills import SKILLS
from vacancy_radar.exchange.nbu import RANGE_URL
from vacancy_radar.normalize import service
from vacancy_radar.normalize.service import (
    NormalizeStats,
    convert_salaries,
    needs_conversion,
    normalize_pending,
    usd_columns,
)
from vacancy_radar.sources import ADAPTERS, RawItem, djinni, dou
from vacancy_radar.sources.http import build_client

DJINNI_ITEMS = djinni.parse_feed(fixture_bytes("djinni/rss_all.xml")).items
DOU_ITEMS = dou.parse_feed(fixture_bytes("dou/rss_all.xml")).items


@pytest.fixture
def nbu() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        for currency in ("usd", "eur"):
            router.get(RANGE_URL, params={"valcode": currency}).respond(
                200, content=fixture_bytes(f"nbu/{currency}_20261030_20261107.json")
            )
        yield router


def store(session: Session, slug: str, items: list[RawItem]) -> None:
    upsert_raw_items(session, ensure_source(session, ADAPTERS[slug]), items)


def run(session: Session, *, rebuild: bool = False, batch_size: int = 500) -> NormalizeStats:
    return normalize_pending(session, rebuild=rebuild, batch_size=batch_size)


def convert(session: Session) -> int:
    with build_client() as client:
        return convert_salaries(session, client, sleep=lambda _: None)


def vacancies(session: Session) -> dict[str, Vacancy]:
    rows = session.execute(
        select(RawVacancy.external_id, Vacancy).join(
            Vacancy, Vacancy.raw_vacancy_id == RawVacancy.id
        )
    )
    return dict(rows.all())


def test_normalizes_every_raw_item_once(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "djinni", DJINNI_ITEMS)

    first = run(db_session)
    second = run(db_session)

    assert first == NormalizeStats(normalized=5, with_salary=2)
    assert second == NormalizeStats()
    assert nbu.calls.call_count == 0  # normalization itself never calls NBU
    assert len(vacancies(db_session)) == 5


def test_converts_each_salary_once(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "djinni", DJINNI_ITEMS)  # one UAH and one USD salary
    run(db_session)

    assert convert(db_session) == 1
    calls = nbu.calls.call_count
    assert convert(db_session) == 0
    assert nbu.calls.call_count == calls == 2  # USD and EUR rates, once


def test_stores_the_normalized_fields(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "djinni", DJINNI_ITEMS)
    run(db_session)
    convert(db_session)

    sales = vacancies(db_session)["852239"]  # "40 000 - 60 000 грн", published 2026-11-05
    assert (sales.salary_min, sales.salary_max, sales.salary_currency) == (40_000, 60_000, "UAH")
    # 40 000 / 45.5375 and 60 000 / 45.5375, the NBU rate of Nov 5
    assert (sales.salary_usd_min, sales.salary_usd_max) == (878, 1318)
    assert sales.category == "Sales Manager"
    assert sales.url == DJINNI_ITEMS[0].url
    assert sales.normalizer_version == NORMALIZER_VERSION


def test_usd_salaries_need_no_rates(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "dou", DOU_ITEMS)

    stats = run(db_session)

    assert stats == NormalizeStats(normalized=6, with_salary=2)
    assert convert(db_session) == 0  # already in dollars after normalization
    assert nbu.calls.call_count == 0
    orients = vacancies(db_session)["379090"]
    assert (orients.company, orients.cities, orients.salary_usd_max) == ("Orients", ["Львів"], 2200)


def test_a_changed_raw_item_is_normalized_again(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "dou", DOU_ITEMS)
    run(db_session)
    changed = DOU_ITEMS[0]
    store(
        db_session,
        "dou",
        [
            RawItem(
                changed.external_id,
                changed.url,
                {**changed.payload, "title": "QA Engineer в Acme, віддалено"},
            )
        ],
    )

    # now() is the start of the transaction, and this test runs in one; a real
    # collection run is a later transaction, so its updated_at is later too.
    db_session.execute(
        update(RawVacancy)
        .where(RawVacancy.external_id == changed.external_id)
        .values(updated_at=RawVacancy.updated_at + timedelta(minutes=30))
    )

    stats = run(db_session)

    assert stats.normalized == 1
    assert vacancies(db_session)[changed.external_id].company == "Acme"


def test_a_new_normalizer_version_rebuilds_everything(
    db_session: Session, nbu: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    store(db_session, "dou", DOU_ITEMS)
    run(db_session)
    monkeypatch.setattr(service, "NORMALIZER_VERSION", NORMALIZER_VERSION + 1)

    assert run(db_session).normalized == 6
    assert {v.normalizer_version for v in vacancies(db_session).values()} == {
        NORMALIZER_VERSION + 1
    }
    assert run(db_session).normalized == 0


def test_rebuild_normalizes_every_item(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "dou", DOU_ITEMS)
    run(db_session)

    assert run(db_session, rebuild=True).normalized == 6


def test_works_through_several_batches(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "dou", DOU_ITEMS)
    store(db_session, "djinni", DJINNI_ITEMS)

    assert run(db_session, batch_size=2).normalized == 11
    assert run(db_session, batch_size=2, rebuild=True).normalized == 11


def test_deleting_a_raw_item_deletes_its_vacancy(
    db_session: Session, nbu: respx.MockRouter
) -> None:
    store(db_session, "dou", DOU_ITEMS)
    run(db_session)

    db_session.execute(delete(RawVacancy).where(RawVacancy.external_id == DOU_ITEMS[0].external_id))

    assert DOU_ITEMS[0].external_id not in vacancies(db_session)
    assert len(vacancies(db_session)) == 5


def test_an_nbu_failure_leaves_salaries_unconverted_until_the_next_run(
    db_session: Session,
) -> None:
    store(db_session, "djinni", DJINNI_ITEMS[:1])  # a UAH salary: needs a rate
    run(db_session)

    with respx.mock:
        respx.get(RANGE_URL).respond(503)
        with pytest.raises(Exception, match="503"):
            convert(db_session)

    sales = vacancies(db_session)["852239"]
    assert sales.salary_min == 40_000  # normalized all the same
    assert (sales.salary_usd_min, sales.salary_usd_max) == (None, None)

    with respx.mock(assert_all_called=False) as router:
        for currency in ("usd", "eur"):
            router.get(RANGE_URL, params={"valcode": currency}).respond(
                200, content=fixture_bytes(f"nbu/{currency}_20261030_20261107.json")
            )
        assert convert(db_session) == 1
    db_session.refresh(sales)
    assert (sales.salary_usd_min, sales.salary_usd_max) == (878, 1318)


def test_a_rebuilt_salary_is_converted_again(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "djinni", DJINNI_ITEMS)
    run(db_session)
    convert(db_session)

    run(db_session, rebuild=True)

    assert convert(db_session) == 1
    sales = vacancies(db_session)["852239"]
    assert (sales.salary_usd_min, sales.salary_usd_max) == (878, 1318)


def test_items_of_sources_without_a_mapping_are_skipped(
    db_session: Session, nbu: respx.MockRouter
) -> None:
    source = Source(slug="workua", name="Work.ua", base_url="https://www.work.ua")
    db_session.add(source)
    db_session.flush()
    upsert_raw_items(db_session, source.id, [RawItem("1", "https://www.work.ua/jobs/1/", {})])

    assert run(db_session) == NormalizeStats()


def test_syncs_the_skill_table_with_the_dictionary(
    db_session: Session, nbu: respx.MockRouter
) -> None:
    db_session.add(Skill(slug="cobol", name="COBOL", category="language"))
    db_session.flush()

    run(db_session)

    slugs = set(db_session.scalars(select(Skill.slug)))
    assert slugs == {skill.slug for skill in SKILLS}
    assert db_session.scalar(select(func.count()).select_from(Skill)) == len(SKILLS)


# --- needs_conversion ----------------------------------------------------------------

RATES: dict[RateCurrency, Decimal] = {"USD": Decimal("41.50"), "EUR": Decimal("48.25")}
# Every currency and period, each as a closed range and as both kinds of open one.
SALARIES = [
    Salary(low, high, currency, period)
    for currency, period, (low, high) in product(
        get_args(Currency), get_args(Period), [(3000, 4000), (3000, None), (None, 4000)]
    )
]


def waits_for_conversion(session: Session, vacancy: Vacancy) -> bool:
    return bool(session.scalar(select(needs_conversion()).where(Vacancy.id == vacancy.id)))


@pytest.mark.parametrize("salary", [None, *SALARIES], ids=str)
def test_needs_conversion_selects_what_conversion_would_fill(
    db_session: Session, salary: Salary | None
) -> None:
    """The SQL predicate agrees with `to_monthly_usd`, which does the converting.

    A vacancy waits exactly when normalization (no rates) left it without
    dollars and conversion (with rates) would give it some. If the two
    disagreed, matching would wait for a salary that never gets converted,
    or count one as unknown before conversion had its chance.
    """
    normalized = to_monthly_usd(salary, rates=None) if salary else None
    converted = to_monthly_usd(salary, RATES) if salary else None
    vacancy = add_vacancy(
        db_session,
        salary_min=salary.min_amount if salary else None,
        salary_max=salary.max_amount if salary else None,
        salary_currency=salary.currency if salary else None,
        salary_period=salary.period if salary else None,
        salary_usd_min=normalized.min_usd if normalized else None,
        salary_usd_max=normalized.max_usd if normalized else None,
    )

    assert waits_for_conversion(db_session, vacancy) is (
        normalized is None and converted is not None
    )

    # Once converted, or when there was nothing to convert, it waits no more.
    db_session.execute(
        update(Vacancy)
        .where(Vacancy.id == vacancy.id)
        .values(**usd_columns(converted or normalized))
    )
    assert not waits_for_conversion(db_session, vacancy)
