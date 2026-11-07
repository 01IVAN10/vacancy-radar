"""Pages that are not part of a feature: the home page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from vacancy_radar.api.deps import OptionalUser
from vacancy_radar.api.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: OptionalUser) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")
