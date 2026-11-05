"""FastAPI application factory."""

from fastapi import FastAPI

from vacancy_radar import __version__
from vacancy_radar.api import health


def create_app() -> FastAPI:
    """Build a new application instance with all routers attached."""
    app = FastAPI(title="Vacancy Radar", version=__version__)
    app.include_router(health.router)
    return app


app = create_app()
