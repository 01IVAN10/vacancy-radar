"""FastAPI application factory."""

from urllib.parse import urlencode

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from vacancy_radar import __version__
from vacancy_radar.api import auth, health, pages
from vacancy_radar.api.deps import LoginRequiredError
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import CSRFCookieMiddleware
from vacancy_radar.auth.tokens import signing_key
from vacancy_radar.config import get_settings


def create_app() -> FastAPI:
    """Build a new application instance with all routers attached.

    Fails at once when `SECRET_KEY` is missing or too short, so a bad
    deployment does not start and serve pages nobody can sign in to.
    """
    settings = get_settings()
    signing_key(settings)

    app = FastAPI(title="Vacancy Radar", version=__version__)
    app.add_middleware(CSRFCookieMiddleware, secure=settings.cookie_secure)
    app.add_exception_handler(LoginRequiredError, _redirect_to_login)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _error_page)  # type: ignore[arg-type]
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(auth.router)
    return app


async def _redirect_to_login(request: Request, exc: LoginRequiredError) -> Response:
    """Send a signed-out visitor to the sign-in page, and back here afterwards.

    HTMX follows redirects inside its own request and would swap the sign-in
    page into a fragment, so it gets `HX-Redirect` to load the page instead.
    """
    target = f"/login?{urlencode({'next': exc.next_path})}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, headers={"HX-Redirect": target})
    return RedirectResponse(target, status.HTTP_303_SEE_OTHER)


async def _error_page(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


app = create_app()
