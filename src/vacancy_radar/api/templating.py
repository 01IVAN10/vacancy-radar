"""Jinja2 templates with the values every page needs."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _page_context(request: Request) -> dict[str, Any]:
    """`csrf_token` for forms and `user` for the navigation, on every page."""
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "user": getattr(request.state, "user", None),
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_page_context])
