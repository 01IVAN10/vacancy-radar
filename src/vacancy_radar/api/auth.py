"""Sign-up, sign-in and sign-out pages.

Forms post back to the same URL. Success redirects with 303 (post, redirect,
get), so reloading the next page never resubmits the form. A form with
errors is rendered again with status 422. Both forms carry `next`, the page
to return to, so it survives a detour from sign-in to sign-up.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from vacancy_radar.api.deps import Config, DbSession, OptionalUser, WebConfig
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import new_csrf_token, set_csrf_cookie, verify_csrf
from vacancy_radar.auth.service import RegistrationError, authenticate, register
from vacancy_radar.auth.session import clear_session_cookie, set_session_cookie

router = APIRouter(tags=["auth"])

HOME = "/"
# Where signing in or up leads when no page asked for it, or `next` is unsafe.
AFTER_SIGN_IN = "/subscriptions"
LOGIN_FAILED = "Wrong email or password."

NextQuery = Annotated[str, Query(alias="next")]
NextField = Annotated[str, Form(alias="next")]


@router.get("/register", response_class=HTMLResponse)
async def register_form(
    request: Request, user: OptionalUser, next_path: NextQuery = AFTER_SIGN_IN
) -> Response:
    target = safe_next(next_path)
    if user is not None:
        return RedirectResponse(target, status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "auth/register.html", {"email": "", "errors": {}, "next": target}
    )


@router.post("/register", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
async def register_submit(
    request: Request,
    session: DbSession,
    config: Config,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next_path: NextField = AFTER_SIGN_IN,
) -> Response:
    target = safe_next(next_path)
    try:
        user = await register(session, email, password)
    except RegistrationError as exc:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {"email": email, "errors": exc.errors, "next": target},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return _signed_in(config, user.id, target)


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request, user: OptionalUser, next_path: NextQuery = AFTER_SIGN_IN
) -> Response:
    target = safe_next(next_path)
    if user is not None:
        return RedirectResponse(target, status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "auth/login.html", {"email": "", "error": None, "next": target}
    )


@router.post("/login", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
async def login_submit(
    request: Request,
    session: DbSession,
    config: Config,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next_path: NextField = AFTER_SIGN_IN,
) -> Response:
    target = safe_next(next_path)
    user = await authenticate(session, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"email": email, "error": LOGIN_FAILED, "next": target},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return _signed_in(config, user.id, target)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(config: Config) -> Response:
    response = RedirectResponse(HOME, status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response, secure=config.cookie_secure)
    return response


def safe_next(target: str) -> str:
    """`target` if it is a path on this site, else `AFTER_SIGN_IN`.

    Anything else would make the sign-in page an open redirect: a link to
    `/login?next=https://evil.example` that sends the user to another site
    right after a real sign-in. A path starting with `/` has no scheme, but
    `//evil.example` and `/\\evil.example` are read by browsers as other hosts.
    """
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return AFTER_SIGN_IN
    # Control characters could split the Location header or hide a host.
    if not target.isprintable():
        return AFTER_SIGN_IN
    return target


def _signed_in(config: WebConfig, user_id: int, target: str) -> Response:
    """Redirect with a new session cookie, and a new CSRF token for the new session."""
    response = RedirectResponse(target, status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, user_id, key=config.secret_key, secure=config.cookie_secure)
    set_csrf_cookie(response, new_csrf_token(), secure=config.cookie_secure)
    return response
