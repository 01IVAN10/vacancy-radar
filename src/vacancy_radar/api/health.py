"""Liveness endpoint used by Docker healthchecks and post-deploy smoke checks."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from vacancy_radar import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


@router.get("/health")
async def health() -> HealthResponse:
    """Report that the process is up. Does not touch the database or Redis."""
    return HealthResponse(status="ok", version=__version__)
