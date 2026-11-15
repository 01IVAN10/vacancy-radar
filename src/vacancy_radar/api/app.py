"""FastAPI application factory."""

from urllib.parse import urlencode

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from vacancy_radar import __version__
from vacancy_radar.api import analytics, auth, health, pages, search, subscriptions, telegram
from vacancy_radar.api.deps import LoginRequiredError, Sessions, WebConfig
from vacancy_radar.api.middleware import CurrentUserMiddleware
from vacancy_radar.api.templating import STATIC_DIR, STATIC_PATH, templates
from vacancy_radar.auth.csrf import CSRFCookieMiddleware
from vacancy_radar.config import Settings, check_production, get_settings
from vacancy_radar.db.engine import get_async_sessionmaker
from vacancy_radar.log import configure_logging


def create_app(settings: Settings | None = None, sessions: Sessions | None = None) -> FastAPI:
    """Build a new application instance with all routers attached.

    `settings` and `sessions` default to the process-wide ones; tests pass
    their own. Fails at once without `SECRET_KEY`, so a bad deployment does
    not start and serve pages nobody can sign in to, and in production
    without an https site URL or with a development key (`check_production`).

    Configures logging like every other process, so events from the web app
    are JSON on stderr in a container. In production the generated API docs
    (`/docs`, `/redoc`, `/openapi.json`) are off: the site is HTML, not an
    API, and Swagger UI loads scripts from a CDN that a Content Security
    Policy would block. They stay on locally.
    """
    settings = settings or get_settings()
    if settings.secret_key is None:
        raise RuntimeError("SECRET_KEY is required for the web app")
    check_production(settings)
    configure_logging(settings.log_level)
    config = WebConfig(
        secret_key=settings.secret_key.get_secret_value(),
        cookie_secure=settings.cookie_secure,
        sessions=sessions or get_async_sessionmaker(),
        telegram_bot_username=settings.telegram_bot_username,
    )

    docs = settings.app_env != "production"
    app = FastAPI(
        title="Vacancy Radar",
        version=__version__,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.config = config
    app.add_middleware(CurrentUserMiddleware, config=config)
    app.add_middleware(CSRFCookieMiddleware, secure=config.cookie_secure)
    app.add_exception_handler(LoginRequiredError, _redirect_to_login)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _error_page)  # type: ignore[arg-type]
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(search.router)
    app.include_router(analytics.router)
    app.include_router(auth.router)
    app.include_router(subscriptions.router)
    app.include_router(telegram.router)
    app.mount(STATIC_PATH, StaticFiles(directory=STATIC_DIR), name="static")
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
