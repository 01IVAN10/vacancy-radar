"""Jinja2 templates with the values every page needs, and how pages write dates and numbers."""

from datetime import date
from pathlib import Path
from typing import Any, Final

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
# Scripts the pages load, served at `STATIC_PATH`.
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_PATH: Final = "/static"

# Spelled out rather than taken from `strftime`, whose names follow the
# process locale: a server with another `LC_TIME` would change the pages.
MONTH_NAMES: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def day_label(day: date, *, year: bool = True) -> str:
    """ "9 Nov 2026", or "9 Nov" without the year."""
    label = f"{day.day} {MONTH_NAMES[day.month - 1][:3]}"
    return f"{label} {day.year}" if year else label


def month_label(month: date) -> str:
    """ "November 2026"."""
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def thousands(number: int) -> str:
    """ "4,046"."""
    return f"{number:,}"


def _page_context(request: Request) -> dict[str, Any]:
    """`csrf_token` for forms and `user` for the navigation, on every page."""
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "user": getattr(request.state, "user", None),
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_page_context])
templates.env.filters |= {"day": day_label, "month": month_label, "thousands": thousands}
