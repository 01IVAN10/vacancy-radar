"""FastAPI application factory."""

from urllib.parse import urlencode

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from vacancy_radar import __version__
from vacancy_radar.api import auth, health, pages, subscriptions
from vacancy_radar.api.deps import LoginRequiredError, Sessions, WebConfig
from vacancy_radar.api.middleware import CurrentUserMiddleware
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import CSRFCookieMiddleware
from vacancy_radar.config import Settings, get_settings
from vacancy_radar.db.engine import get_async_sessionmaker


def create_app(settings: Settings | None = None, sessions: Sessions | None = None) -> FastAPI:
    """Build a new application instance with all routers attached.

    `settings` and `sessions` default to the process-wide ones; tests pass
    their own. Fails at once without `SECRET_KEY`, so a bad deployment does
    not start and serve pages nobody can sign in to.
    """
    settings = settings or get_settings()
    if settings.secret_key is None:
        raise RuntimeError("SECRET_KEY is required for the web app")
    config = WebConfig(
        secret_key=settings.secret_key.get_secret_value(),
        cookie_secure=settings.cookie_secure,
        sessions=sessions or get_async_sessionmaker(),
    )

    app = FastAPI(title="Vacancy Radar", version=__version__)
    app.state.config = config
    app.add_middleware(CurrentUserMiddleware, config=config)
    app.add_middleware(CSRFCookieMiddleware, secure=config.cookie_secure)
    app.add_exception_handler(LoginRequiredError, _redirect_to_login)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _error_page)  # type: ignore[arg-type]
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(subscriptions.router)
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


async def _error_page(request: Request, exc: StarletteHTTPException) -> Response:
    """An HTML error page for browsers, FastAPI's JSON error for everyone else."""
    if "text/html" not in request.headers.get("accept", ""):
        return await http_exception_handler(request, exc)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


app = create_app()
