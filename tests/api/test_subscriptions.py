import re
from dataclasses import replace
from typing import Any, get_args

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import add_user, csrf_token, sign_in
from vacancy_radar.api.subscriptions import CATEGORY_LABELS, describe
from vacancy_radar.db.models import Subscription, User
from vacancy_radar.domain.skills import SKILLS, Category
from vacancy_radar.domain.subscriptions import SalaryFloor, SubscriptionFilter
from vacancy_radar.subscriptions.service import MAX_SUBSCRIPTIONS

pytestmark = pytest.mark.anyio

FormData = dict[str, str | list[str]]

FORM: FormData = {
    "name": "Python remote",
    "skills": ["python", "django"],
    "skills_match": "all",
    "seniority": ["senior"],
    "work_formats": ["remote"],
    "min_salary_usd": "3000",
    "include_unsalaried": "on",
}
HTMX = {"HX-Request": "true"}


@pytest.fixture
async def owner(client: AsyncClient, async_session: AsyncSession, password_hash: str) -> User:
    """The signed-in user."""
    user = await add_user(async_session, password_hash, "owner@example.com")
    sign_in(client, user)
    return user


@pytest.fixture
async def strangers(async_session: AsyncSession, password_hash: str) -> Subscription:
    """A subscription of another account."""
    stranger = await add_user(async_session, password_hash, "stranger@example.com")
    subscription = row(stranger.id, name="Not yours", skills=["go"])
    async_session.add(subscription)
    await async_session.commit()
    return subscription


def row(user_id: int, **changes: Any) -> Subscription:
    values: dict[str, Any] = {
        "name": "Python remote",
        "skills": ["python"],
        "skills_match": "any",
        "seniority": [],
        "work_formats": [],
    }
    return Subscription(user_id=user_id, **(values | changes))


async def post(
    client: AsyncClient,
    url: str,
    data: FormData | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    form = {"csrf_token": await csrf_token(client), **(data or {})}
    return await client.post(url, data=form, headers=headers)


async def create(client: AsyncClient, **changes: str | list[str]) -> Response:
    return await post(client, "/subscriptions", FORM | changes)


async def stored(async_session: AsyncSession) -> list[Subscription]:
    rows = await async_session.scalars(
        select(Subscription).order_by(Subscription.id).execution_options(populate_existing=True)
    )
    return list(rows)


async def fill_to_the_limit(async_session: AsyncSession, owner: User) -> None:
    owner_id = owner.id
    async_session.add_all(row(owner_id, name=f"#{number}") for number in range(MAX_SUBSCRIPTIONS))
    await async_session.commit()


async def test_signed_out_visitor_is_sent_to_sign_in(client: AsyncClient) -> None:
    for response in (
        await client.get("/subscriptions"),
        await client.get("/subscriptions/new"),
        # Sign-in is checked before CSRF, so a signed-out POST is not a 403.
        await client.post("/subscriptions", data=FORM),
    ):
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=%2Fsubscriptions")


async def test_empty_list_offers_a_first_subscription(client: AsyncClient, owner: User) -> None:
    response = await client.get("/subscriptions")

    assert response.status_code == 200
    assert "You have no subscriptions yet." in response.text
    assert 'href="/subscriptions/new"' in response.text


async def test_new_form_has_every_skill_and_sensible_defaults(
    client: AsyncClient, owner: User
) -> None:
    response = await client.get("/subscriptions/new")

    assert response.status_code == 200
    assert "New subscription" in response.text
    assert 'action="/subscriptions"' in response.text
    assert response.text.count('name="skills"') == len(SKILLS)
    assert re.search(r'value="any"\s+checked', response.text)
    assert re.search(r'name="include_unsalaried"\s+checked', response.text)


def test_every_skill_category_has_a_form_group() -> None:
    # A category without a label would drop its skills from the form.
    assert set(CATEGORY_LABELS) == set(get_args(Category))


async def test_create_stores_the_subscription_and_lists_it(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    response = await create(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/subscriptions"
    [subscription] = await stored(async_session)
    assert subscription.user_id == owner.id
    assert subscription.skills == ["python", "django"]
    assert subscription.skills_match == "all"
    assert (subscription.min_salary_usd, subscription.include_unsalaried) == (3000, True)
    page = await client.get("/subscriptions")
    assert "Python remote" in page.text
    assert "all of Python, Django · Senior · Remote · from $3,000/month or not stated" in page.text


async def test_unticked_checkbox_means_no(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    form = {key: value for key, value in FORM.items() if key != "include_unsalaried"}

    await post(client, "/subscriptions", form)

    [subscription] = await stored(async_session)
    assert subscription.include_unsalaried is False


async def test_without_a_minimum_the_checkbox_is_not_stored(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    form = {key: value for key, value in FORM.items() if key != "include_unsalaried"}

    await post(client, "/subscriptions", form | {"min_salary_usd": ""})

    [subscription] = await stored(async_session)
    assert (subscription.min_salary_usd, subscription.include_unsalaried) == (None, None)


async def test_invalid_form_is_shown_again_with_errors(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    response = await create(client, name="", min_salary_usd="lots")

    assert response.status_code == 422
    assert "Give the subscription a name." in response.text
    assert "Enter a whole number of dollars" in response.text
    # What the user entered is kept.
    assert 'value="lots"' in response.text
    assert re.search(r'value="django"\s+checked', response.text)
    assert await stored(async_session) == []


async def test_a_subscription_needs_a_criterion(client: AsyncClient, owner: User) -> None:
    response = await post(client, "/subscriptions", {"name": "Everything"})

    assert response.status_code == 422
    assert "Choose at least one skill" in response.text


async def test_at_the_limit_the_list_and_the_new_page_say_so(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await fill_to_the_limit(async_session, owner)

    page = await client.get("/subscriptions")
    new = await client.get("/subscriptions/new")

    assert "reached the limit of 20 subscriptions" in page.text
    assert 'href="/subscriptions/new"' not in page.text
    # Before any form is filled in.
    assert new.status_code == 200
    assert "reached the limit of 20 subscriptions" in new.text
    assert 'name="name"' not in new.text


async def test_create_at_the_limit_is_refused(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await fill_to_the_limit(async_session, owner)

    response = await create(client, name="one too many")

    assert response.status_code == 422
    assert "reached the limit of 20 subscriptions" in response.text
    assert 'value="one too many"' in response.text
    assert len(await stored(async_session)) == MAX_SUBSCRIPTIONS


async def test_edit_form_is_filled_from_the_subscription(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await create(client)
    [subscription] = await stored(async_session)

    response = await client.get(f"/subscriptions/{subscription.id}/edit")

    assert response.status_code == 200
    assert "Edit subscription" in response.text
    assert f'action="/subscriptions/{subscription.id}"' in response.text
    assert 'value="Python remote"' in response.text
    assert re.search(r'value="all"\s+checked', response.text)
    assert 'value="3000"' in response.text


async def test_update_saves_the_changes(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await create(client)
    [subscription] = await stored(async_session)

    response = await post(
        client,
        f"/subscriptions/{subscription.id}",
        {"name": "Any junior", "seniority": ["junior"], "skills_match": "all"},
    )

    assert response.status_code == 303
    [updated] = await stored(async_session)
    assert updated.name == "Any junior"
    assert (updated.skills, updated.skills_match, updated.seniority) == ([], "any", ["junior"])
    assert (updated.min_salary_usd, updated.include_unsalaried) == (None, None)


async def test_invalid_update_changes_nothing(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await create(client)
    [subscription] = await stored(async_session)

    response = await post(client, f"/subscriptions/{subscription.id}", FORM | {"name": ""})

    assert response.status_code == 422
    assert "Edit subscription" in response.text
    [unchanged] = await stored(async_session)
    assert unchanged.name == "Python remote"


async def test_toggle_pauses_and_resumes(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await create(client)
    [subscription] = await stored(async_session)

    response = await post(client, f"/subscriptions/{subscription.id}/toggle")

    assert response.status_code == 303
    [paused] = await stored(async_session)
    assert paused.is_active is False
    assert "Paused" in (await client.get("/subscriptions")).text


async def test_htmx_toggle_returns_the_list_without_the_page(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await create(client)
    [subscription] = await stored(async_session)

    response = await post(client, f"/subscriptions/{subscription.id}/toggle", headers=HTMX)

    assert response.status_code == 200
    assert '<div id="subscriptions">' in response.text
    assert "<html" not in response.text
    assert f'<tr id="subscription-{subscription.id}">' in response.text
    assert "Paused" in response.text
    assert "Resume" in response.text


async def test_delete_removes_the_subscription(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await create(client)
    [subscription] = await stored(async_session)

    response = await post(client, f"/subscriptions/{subscription.id}/delete")

    assert response.status_code == 303
    assert await stored(async_session) == []


async def test_htmx_delete_of_the_last_one_shows_the_empty_list(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await create(client)
    [subscription] = await stored(async_session)

    response = await post(client, f"/subscriptions/{subscription.id}/delete", headers=HTMX)

    assert response.status_code == 200
    assert "<html" not in response.text
    assert "You have no subscriptions yet." in response.text
    assert "<table>" not in response.text
    assert await stored(async_session) == []


async def test_htmx_delete_at_the_limit_offers_a_new_subscription_again(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    await fill_to_the_limit(async_session, owner)
    first = (await stored(async_session))[0]

    response = await post(client, f"/subscriptions/{first.id}/delete", headers=HTMX)

    assert "reached the limit" not in response.text
    assert 'href="/subscriptions/new"' in response.text
    assert response.text.count('<tr id="subscription-') == MAX_SUBSCRIPTIONS - 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/edit"),
        ("POST", ""),
        ("POST", "/toggle"),
        ("POST", "/delete"),
    ],
)
async def test_another_accounts_subscription_is_not_found(
    client: AsyncClient,
    async_session: AsyncSession,
    owner: User,
    strangers: Subscription,
    method: str,
    path: str,
) -> None:
    stranger_id = strangers.id
    url = f"/subscriptions/{stranger_id}{path}"
    missing_url = f"/subscriptions/{stranger_id + 1000}{path}"

    if method == "GET":
        response, missing = await client.get(url), await client.get(missing_url)
    else:
        response, missing = await post(client, url, FORM), await post(client, missing_url, FORM)

    # Exactly like a subscription that does not exist, so ids reveal nothing.
    assert response.status_code == missing.status_code == 404
    assert response.text == missing.text
    theirs = await async_session.get(Subscription, stranger_id, populate_existing=True)
    assert theirs is not None
    assert (theirs.name, theirs.is_active) == ("Not yours", True)


async def test_invalid_update_of_another_accounts_subscription_is_not_found(
    client: AsyncClient, owner: User, strangers: Subscription
) -> None:
    response = await post(client, f"/subscriptions/{strangers.id}", {"name": ""})

    assert response.status_code == 404


async def test_list_does_not_show_other_accounts_subscriptions(
    client: AsyncClient, owner: User, strangers: Subscription
) -> None:
    response = await client.get("/subscriptions")

    assert "Not yours" not in response.text


@pytest.mark.parametrize("path", ["", "/1", "/1/toggle", "/1/delete"])
async def test_writes_need_a_csrf_token(client: AsyncClient, owner: User, path: str) -> None:
    await csrf_token(client)

    response = await client.post(f"/subscriptions{path}", data=FORM)

    assert response.status_code == 403


async def test_skills_removed_from_the_dictionary_are_shown_as_untracked(
    client: AsyncClient, async_session: AsyncSession, owner: User
) -> None:
    async_session.add(row(owner.id, name="Legacy", skills=["python", "removed-skill"]))
    await async_session.commit()

    response = await client.get("/subscriptions")

    assert response.status_code == 200
    assert "any of Python, removed-skill (no longer tracked)" in response.text


NOTHING = SubscriptionFilter(
    skills=(), skills_match="any", seniority=(), work_formats=(), salary=None
)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"skills": ("python", "go")}, ["any of Python, Go"]),
        ({"skills": ("python", "go"), "skills_match": "all"}, ["all of Python, Go"]),
        ({"skills": ("python",)}, ["Python"]),
        (
            {"seniority": ("junior", "middle"), "work_formats": ("hybrid",)},
            ["Junior, Middle", "Hybrid"],
        ),
        ({"salary": SalaryFloor(2500, include_unsalaried=False)}, ["from $2,500/month"]),
        (
            {"salary": SalaryFloor(2500, include_unsalaried=True)},
            ["from $2,500/month or not stated"],
        ),
    ],
)
def test_describe_lists_each_criterion(changes: dict[str, Any], expected: list[str]) -> None:
    assert describe(replace(NOTHING, **changes)) == expected
