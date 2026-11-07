from http.cookies import SimpleCookie

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import PASSWORD, TEST_SECRET_KEY, add_user, csrf_token, sign_in
from vacancy_radar.api.app import create_app
from vacancy_radar.api.auth import AFTER_SIGN_IN
from vacancy_radar.api.deps import Sessions
from vacancy_radar.auth.csrf import CSRF_COOKIE, CSRF_HEADER
from vacancy_radar.auth.service import EMAIL_TAKEN
from vacancy_radar.auth.session import SESSION_COOKIE
from vacancy_radar.auth.tokens import issue_token
from vacancy_radar.config import get_settings
from vacancy_radar.db.models import User

pytestmark = pytest.mark.anyio


def set_cookie(response: Response, name: str) -> SimpleCookie:
    """The `Set-Cookie` header for `name`, parsed with its attributes."""
    for header in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie(header)
        if name in cookie:
            return cookie
    raise AssertionError(f"no Set-Cookie for {name}: {response.headers}")


async def post_form(client: AsyncClient, url: str, data: dict[str, str]) -> Response:
    return await client.post(url, data={"csrf_token": await csrf_token(client), **data})


@pytest.fixture
def protected() -> str:
    """A page that requires sign-in."""
    return "/subscriptions"


async def test_first_page_view_sets_the_csrf_cookie(client: AsyncClient) -> None:
    response = await client.get("/")

    cookie = set_cookie(response, CSRF_COOKIE)[CSRF_COOKIE]
    assert cookie["httponly"]
    assert cookie["samesite"] == "lax"
    assert not cookie["secure"]


@pytest.mark.parametrize("url", ["/register", "/login"])
async def test_forms_carry_the_csrf_token(client: AsyncClient, url: str) -> None:
    token = await csrf_token(client)

    response = await client.get(url)

    assert response.status_code == 200
    assert f'name="csrf_token" value="{token}"' in response.text
    # A page with the cookie already set does not set it again.
    assert "set-cookie" not in response.headers


async def test_health_does_not_set_a_csrf_cookie(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert "set-cookie" not in response.headers


async def test_register_signs_the_user_in(client: AsyncClient, async_session: AsyncSession) -> None:
    old_csrf = await csrf_token(client)

    response = await post_form(
        client, "/register", {"email": "Ivan@Example.com", "password": PASSWORD}
    )

    assert response.status_code == 303
    assert response.headers["location"] == AFTER_SIGN_IN
    session_cookie = set_cookie(response, SESSION_COOKIE)[SESSION_COOKIE]
    assert session_cookie["httponly"]
    assert session_cookie["samesite"] == "lax"
    assert not session_cookie["secure"]
    assert int(session_cookie["max-age"]) == 7 * 24 * 3600
    # A new session gets a new CSRF token.
    assert client.cookies[CSRF_COOKIE] != old_csrf
    assert await async_session.scalar(select(User.email)) == "ivan@example.com"
    home = await client.get("/")
    assert "You are signed in as ivan@example.com" in home.text


async def test_register_shows_field_errors(client: AsyncClient) -> None:
    response = await post_form(client, "/register", {"email": "ivan", "password": "short"})

    assert response.status_code == 422
    assert "Enter a valid email" in response.text
    assert "Use at least 8 characters" in response.text
    # The email is kept, the password is not echoed back.
    assert 'value="ivan"' in response.text
    assert SESSION_COOKIE not in client.cookies


async def test_register_rejects_a_taken_email(
    client: AsyncClient, async_session: AsyncSession, password_hash: str
) -> None:
    await add_user(async_session, password_hash, "ivan@example.com")

    response = await post_form(
        client, "/register", {"email": "IVAN@example.com", "password": PASSWORD}
    )

    assert response.status_code == 422
    assert EMAIL_TAKEN in response.text


async def test_register_escapes_what_it_echoes(client: AsyncClient) -> None:
    response = await post_form(
        client, "/register", {"email": '"><script>alert(1)</script>', "password": "x"}
    )

    assert "<script>alert(1)" not in response.text
    assert "&lt;script&gt;" in response.text


async def test_login_signs_the_user_in_and_follows_next(
    client: AsyncClient, async_session: AsyncSession, password_hash: str
) -> None:
    await add_user(async_session, password_hash, "ivan@example.com")

    response = await post_form(
        client,
        "/login",
        {"email": "ivan@example.com", "password": PASSWORD, "next": "/protected?page=2"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/protected?page=2"
    assert SESSION_COOKIE in client.cookies


@pytest.mark.parametrize(
    ("email", "password"),
    [("ivan@example.com", "wrong password"), ("nobody@example.com", PASSWORD)],
)
async def test_login_failure_does_not_tell_which_part_was_wrong(
    client: AsyncClient,
    async_session: AsyncSession,
    password_hash: str,
    email: str,
    password: str,
) -> None:
    await add_user(async_session, password_hash, "ivan@example.com")

    response = await post_form(client, "/login", {"email": email, "password": password})

    assert response.status_code == 422
    assert "Wrong email or password." in response.text
    assert SESSION_COOKIE not in client.cookies


async def test_logout_deletes_the_session_cookie(
    client: AsyncClient, async_session: AsyncSession, password_hash: str
) -> None:
    sign_in(client, await add_user(async_session, password_hash))

    response = await post_form(client, "/logout", {})

    assert response.status_code == 303
    cookie = set_cookie(response, SESSION_COOKIE)[SESSION_COOKIE]
    assert cookie.value == ""
    assert int(cookie["max-age"]) == 0
    assert SESSION_COOKIE not in client.cookies


async def test_signed_in_user_skips_the_sign_in_pages(
    client: AsyncClient, async_session: AsyncSession, password_hash: str
) -> None:
    sign_in(client, await add_user(async_session, password_hash))

    for url in ("/login?next=/protected", "/register"):
        response = await client.get(url)

        assert response.status_code == 303
    assert response.headers["location"] == AFTER_SIGN_IN


async def test_protected_page_redirects_to_sign_in_and_back(
    client: AsyncClient, protected: str
) -> None:
    response = await client.get(f"{protected}?page=2")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fsubscriptions%3Fpage%3D2"
    login = await client.get(response.headers["location"])
    assert 'name="next" value="/subscriptions?page=2"' in login.text


async def test_protected_page_tells_htmx_to_load_the_sign_in_page(
    client: AsyncClient, protected: str
) -> None:
    response = await client.get(protected, headers={"HX-Request": "true"})

    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login?next=%2Fsubscriptions"


async def test_protected_page_serves_the_signed_in_user(
    client: AsyncClient, async_session: AsyncSession, password_hash: str, protected: str
) -> None:
    sign_in(client, await add_user(async_session, password_hash, "ivan@example.com"))

    response = await client.get(protected)

    assert response.status_code == 200
    assert "ivan@example.com" in response.text


async def test_garbage_session_cookie_means_signed_out(client: AsyncClient, protected: str) -> None:
    client.cookies.set(SESSION_COOKIE, "garbage")

    response = await client.get(protected)

    assert response.status_code == 303


async def test_session_of_a_deleted_account_means_signed_out(
    client: AsyncClient, protected: str
) -> None:
    client.cookies.set(SESSION_COOKIE, issue_token(999_999, key=TEST_SECRET_KEY))

    response = await client.get(protected)

    assert response.status_code == 303


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "relative/path",
        "/\nSet-Cookie:x",
        "",
    ],
)
async def test_login_never_redirects_off_site(
    client: AsyncClient, async_session: AsyncSession, password_hash: str, target: str
) -> None:
    await add_user(async_session, password_hash, "ivan@example.com")

    response = await post_form(
        client, "/login", {"email": "ivan@example.com", "password": PASSWORD, "next": target}
    )

    assert response.headers["location"] == AFTER_SIGN_IN


@pytest.mark.parametrize("url", ["/register", "/login", "/logout"])
async def test_post_without_csrf_token_is_forbidden(client: AsyncClient, url: str) -> None:
    await csrf_token(client)

    response = await client.post(url, data={"email": "ivan@example.com", "password": PASSWORD})

    assert response.status_code == 403
    assert "The form expired" in response.text


async def test_post_with_a_token_that_does_not_match_the_cookie_is_forbidden(
    client: AsyncClient,
) -> None:
    await csrf_token(client)

    response = await client.post("/logout", data={"csrf_token": "attacker-chosen"})

    assert response.status_code == 403


async def test_post_without_csrf_cookie_is_forbidden(client: AsyncClient) -> None:
    response = await client.post("/logout", data={"csrf_token": "anything"})

    assert response.status_code == 403


async def test_csrf_header_is_accepted_for_htmx(client: AsyncClient) -> None:
    token = await csrf_token(client)

    response = await client.post("/logout", headers={CSRF_HEADER: token})

    assert response.status_code == 303


async def test_production_cookies_are_secure(
    async_session: AsyncSession, test_sessions: Sessions, password_hash: str
) -> None:
    settings = get_settings().model_copy(update={"app_env": "production"})
    app = create_app(settings, sessions=test_sessions)
    await add_user(async_session, password_hash, "ivan@example.com")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        page = await client.get("/login")
        signed_in = await post_form(
            client, "/login", {"email": "ivan@example.com", "password": PASSWORD}
        )
        signed_out = await post_form(client, "/logout", {})

    assert set_cookie(page, CSRF_COOKIE)[CSRF_COOKIE]["secure"]
    assert set_cookie(signed_in, SESSION_COOKIE)[SESSION_COOKIE]["secure"]
    assert set_cookie(signed_in, CSRF_COOKIE)[CSRF_COOKIE]["secure"]
    assert set_cookie(signed_out, SESSION_COOKIE)[SESSION_COOKIE]["secure"]


def test_app_does_not_start_without_a_secret_key() -> None:
    settings = get_settings().model_copy(update={"secret_key": None})

    with pytest.raises(RuntimeError, match="SECRET_KEY is required"):
        create_app(settings)


async def test_sign_out_ends_a_real_session(client: AsyncClient, protected: str) -> None:
    signed_up = await post_form(
        client, "/register", {"email": "ivan@example.com", "password": PASSWORD}
    )
    assert (await client.get(protected)).status_code == 200

    signed_out = await post_form(client, "/logout", {})

    assert (await client.get(protected)).status_code == 303
    # A browser replaces a cookie only when its name, path and domain match.
    written = set_cookie(signed_up, SESSION_COOKIE)[SESSION_COOKIE]
    erased = set_cookie(signed_out, SESSION_COOKIE)[SESSION_COOKIE]
    for attribute in ("path", "domain", "httponly", "samesite", "secure"):
        assert erased[attribute] == written[attribute], attribute


async def test_signed_in_user_sees_their_navigation_on_error_pages(
    client: AsyncClient, async_session: AsyncSession, password_hash: str
) -> None:
    sign_in(client, await add_user(async_session, password_hash, "ivan@example.com"))
    await csrf_token(client)
    html = {"Accept": "text/html"}

    not_found = await client.get("/does-not-exist", headers=html)
    forbidden = await client.post("/logout", data={"csrf_token": "stale"}, headers=html)

    for response, status_code in ((not_found, 404), (forbidden, 403)):
        assert response.status_code == status_code
        assert response.headers["content-type"].startswith("text/html")
        assert "ivan@example.com" in response.text
        assert "Sign out" in response.text
        assert "Sign up" not in response.text


async def test_errors_stay_json_for_clients_that_do_not_ask_for_html(
    client: AsyncClient,
) -> None:
    not_found = await client.get("/does-not-exist", headers={"Accept": "application/json"})
    not_allowed = await client.post("/health")

    assert not_found.status_code == 404
    assert not_found.json() == {"detail": "Not Found"}
    assert not_allowed.status_code == 405
    assert not_allowed.json() == {"detail": "Method Not Allowed"}


async def test_next_survives_the_detour_from_sign_in_to_sign_up(client: AsyncClient) -> None:
    login = await client.get("/login?next=%2Fprotected%3Fpage%3D2%26sort%3Dnew")
    # `/` may stay as it is in a query; `?` and `&` would end or split `next`.
    assert 'href="/register?next=/protected%3Fpage%3D2%26sort%3Dnew"' in login.text

    register = await client.get("/register?next=%2Fprotected%3Fpage%3D2%26sort%3Dnew")
    assert 'name="next" value="/protected?page=2&amp;sort=new"' in register.text
    assert 'href="/login?next=/protected%3Fpage%3D2%26sort%3Dnew"' in register.text

    response = await post_form(
        client,
        "/register",
        {"email": "ivan@example.com", "password": PASSWORD, "next": "/protected?page=2&sort=new"},
    )
    assert response.headers["location"] == "/protected?page=2&sort=new"


async def test_failed_sign_up_keeps_next(client: AsyncClient) -> None:
    response = await post_form(
        client, "/register", {"email": "ivan", "password": PASSWORD, "next": "/protected"}
    )

    assert response.status_code == 422
    assert 'name="next" value="/protected"' in response.text


async def test_sign_up_never_redirects_off_site(client: AsyncClient) -> None:
    response = await post_form(
        client,
        "/register",
        {"email": "ivan@example.com", "password": PASSWORD, "next": "//evil.example"},
    )

    assert response.headers["location"] == AFTER_SIGN_IN


async def test_htmx_returns_to_the_page_not_the_fragment(
    client: AsyncClient, protected: str
) -> None:
    response = await client.get(
        protected,
        headers={"HX-Request": "true", "HX-Current-URL": "http://test/subscriptions?page=2"},
    )

    assert response.headers["HX-Redirect"] == "/login?next=%2Fsubscriptions%3Fpage%3D2"


async def test_empty_csrf_cookie_is_replaced(client: AsyncClient) -> None:
    client.cookies.set(CSRF_COOKIE, "")

    response = await client.get("/")

    assert set_cookie(response, CSRF_COOKIE)[CSRF_COOKIE].value != ""
