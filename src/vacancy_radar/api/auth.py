"""Sign-up, sign-in and sign-out pages.

Forms post back to the same URL. Success redirects with 303 (post, redirect,
get), so reloading the next page never resubmits the form. A form with
errors is rendered again with status 422.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from vacancy_radar.api.deps import SESSION_COOKIE, DbSession, OptionalUser
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import new_csrf_token, set_csrf_cookie, verify_csrf
from vacancy_radar.auth.service import RegistrationError, authenticate, register
from vacancy_radar.auth.tokens import TOKEN_TTL, issue_token, signing_key
from vacancy_radar.config import get_settings

router = APIRouter(tags=["auth"])

HOME = "/"
LOGIN_FAILED = "Wrong email or password."


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request, user: OptionalUser) -> Response:
    if user is not None:
        return RedirectResponse(HOME, status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "auth/register.html", {"email": "", "errors": {}})


@router.post("/register", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
async def register_submit(
    request: Request,
    session: DbSession,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> Response:
    try:
        user = await register(session, email, password)
    except RegistrationError as exc:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {"email": email, "errors": exc.errors},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return _signed_in(user.id, HOME)


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    user: OptionalUser,
    next_path: Annotated[str, Query(alias="next")] = HOME,
) -> Response:
    if user is not None:
        return RedirectResponse(safe_next(next_path), status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "auth/login.html", {"email": "", "error": None, "next": safe_next(next_path)}
    )


@router.post("/login", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
async def login_submit(
    request: Request,
    session: DbSession,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next_path: Annotated[str, Form(alias="next")] = HOME,
) -> Response:
    user = await authenticate(session, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"email": email, "error": LOGIN_FAILED, "next": safe_next(next_path)},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return _signed_in(user.id, safe_next(next_path))


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout() -> Response:
    response = RedirectResponse(HOME, status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
    )
    return response


def safe_next(target: str) -> str:
    """`target` if it is a path on this site, else the home page.

    Anything else would make the sign-in page an open redirect: a link to
    `/login?next=https://evil.example` that sends the user to another site
    right after a real sign-in. A path starting with `/` has no scheme, but
    `//evil.example` and `/\\evil.example` are read by browsers as other hosts.
    """
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return HOME
    # Control characters could split the Location header or hide a host.
    if not target.isprintable():
        return HOME
    return target


def _signed_in(user_id: int, target: str) -> Response:
    """Redirect with a new session cookie, and a new CSRF token for the new session."""
    settings = get_settings()
    response = RedirectResponse(target, status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        issue_token(user_id, key=signing_key(settings)),
        max_age=int(TOKEN_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    set_csrf_cookie(response, new_csrf_token(), secure=settings.cookie_secure)
    return response
