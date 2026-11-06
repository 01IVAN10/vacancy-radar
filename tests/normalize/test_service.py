"""Reprocessing against real Postgres, with NBU mocked by respx."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
import respx
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from tests.support import fixture_bytes
from vacancy_radar.collect.service import ensure_source, upsert_raw_items
from vacancy_radar.db.models import RawVacancy, Skill, Source, Vacancy
from vacancy_radar.domain.normalize import NORMALIZER_VERSION
from vacancy_radar.domain.skills import SKILLS
from vacancy_radar.exchange.nbu import RANGE_URL
from vacancy_radar.normalize import service
from vacancy_radar.normalize.service import NormalizeStats, normalize_pending, pending_batch
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
    with build_client() as client:
        return normalize_pending(
            session, client, rebuild=rebuild, batch_size=batch_size, sleep=lambda _: None
        )


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
    calls = nbu.calls.call_count
    second = run(db_session)

    assert first == NormalizeStats(normalized=5, with_salary=2, with_usd=2)
    assert second == NormalizeStats()
    assert nbu.calls.call_count == calls == 2  # one UAH salary: USD and EUR rates once
    assert len(vacancies(db_session)) == 5


def test_stores_the_normalized_fields(db_session: Session, nbu: respx.MockRouter) -> None:
    store(db_session, "djinni", DJINNI_ITEMS)
    run(db_session)

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

    assert stats == NormalizeStats(normalized=6, with_salary=2, with_usd=2)
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


def test_an_nbu_failure_leaves_the_batch_pending(db_session: Session) -> None:
    store(db_session, "djinni", DJINNI_ITEMS[:1])  # a UAH salary: needs a rate

    with respx.mock:
        respx.get(RANGE_URL).respond(503)
        with pytest.raises(Exception, match="503"):
            run(db_session)

    assert vacancies(db_session) == {}
    assert len(pending_batch(db_session, after_id=0, rebuild=False, limit=10)) == 1


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
