"""The market analytics page, open to everyone.

`GET /analytics?month=&level=` shows three aggregates of `read_analytics`:
top skills of a month, weekly salary quartiles of a level, and new jobs per
week. Each chart has a table with the same numbers, so the page works and
reads without JavaScript. The charts are drawn by `static/analytics.js` from
a JSON block in the page; the page has no inline script, so a Content
Security Policy can forbid inline scripts later.

The choices are a plain GET form: a link that can be shared, and the
browser's Back button restores both the page and the form. A hidden median
is never sent, neither in a table nor in the JSON, so no one can read it
from the page source.
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse

from vacancy_radar.analytics.service import Analytics, JobsWeek, read_analytics
from vacancy_radar.api.deps import DbSession, Now
from vacancy_radar.api.labels import LEVEL_LABELS
from vacancy_radar.api.templating import templates
from vacancy_radar.domain.analytics import (
    EMPTY_ANALYTICS_FORM,
    MIN_SAMPLE,
    AnalyticsForm,
    InvalidAnalyticsError,
    SalaryWeek,
    build_analytics_query,
    month_param,
    share,
)
from vacancy_radar.domain.normalize import KYIV

router = APIRouter(tags=["analytics"])


def submitted_choices(
    month: str = EMPTY_ANALYTICS_FORM.month, level: str = EMPTY_ANALYTICS_FORM.level
) -> AnalyticsForm:
    """The choices from the query string; a missing one takes `AnalyticsForm`'s default."""
    return AnalyticsForm(month=month, level=level)


Submitted = Annotated[AnalyticsForm, Depends(submitted_choices)]


@dataclass(frozen=True, slots=True)
class SalaryRow:
    week: date
    salaries: SalaryWeek


@dataclass(frozen=True, slots=True)
class JobsRow:
    week: date
    total: JobsWeek
    # Jobs that week by source slug, 0 for a source without any.
    by_source: dict[str, int]


@router.get("/analytics", response_class=HTMLResponse, name="analytics")
async def analytics_page(
    request: Request, session: DbSession, now: Now, form: Submitted
) -> HTMLResponse:
    context: dict[str, Any] = {
        "form": form,
        "errors": {},
        "data": None,
        "levels": LEVEL_LABELS,
        "day": day_label,
    }
    try:
        query = build_analytics_query(form)
    except InvalidAnalyticsError as exc:
        context["errors"] = exc.errors
    else:
        data = await read_analytics(session, now, month=query.month, level=query.level)
        if data is not None:
            context |= _page_context(data, query.month)
    return templates.TemplateResponse(
        request,
        "analytics/page.html",
        context,
        status_code=(
            status.HTTP_422_UNPROCESSABLE_CONTENT if context["errors"] else status.HTTP_200_OK
        ),
    )


def _page_context(data: Analytics, month: date | None) -> dict[str, Any]:
    summary = data.summary
    refreshed = summary.refreshed_at.astimezone(KYIV)
    # The month asked for, or the newest one `read_analytics` picked.
    chosen = month or (data.top_skills.month if data.top_skills else None)
    salaries = [SalaryRow(week.week, week) for week in data.salaries]
    jobs = _jobs_rows(data)
    return {
        "data": data,
        "min_sample": MIN_SAMPLE,
        "months": {month_param(m): f"{m:%B %Y}" for m in data.months},
        "chosen_month": month_param(chosen) if chosen else "",
        "chosen_month_label": f"{chosen:%B %Y}" if chosen else "",
        "level_label": LEVEL_LABELS[data.level] if data.level else "Every level",
        "first_day": day_label(summary.first_day),
        "refreshed": f"{day_label(refreshed.date())}, {refreshed:%H:%M} Kyiv time",
        "salary_share": share(summary.with_salary, summary.with_skills),
        "source_names": {slug: site.name for slug, site in data.sources.items()},
        "salaries": salaries,
        "salaries_settle_from": _first_settling(
            [(row.week, row.salaries.settling) for row in salaries]
        ),
        "jobs": jobs,
        "jobs_settle_from": _first_settling([(row.week, row.total.settling) for row in jobs]),
        "chart_data": _chart_data(data, jobs),
    }


def _jobs_rows(data: Analytics) -> list[JobsRow]:
    """A row per week of the total, which has every week any source has."""
    by_source = {
        slug: {week.week: week.jobs for week in weeks}
        for slug, weeks in data.jobs_by_source.items()
    }
    return [
        JobsRow(
            week=total.week,
            total=total,
            by_source={slug: by_source.get(slug, {}).get(total.week, 0) for slug in data.sources},
        )
        for total in data.jobs_total
    ]


def _chart_data(data: Analytics, jobs: list[JobsRow]) -> dict[str, Any]:
    """What `static/analytics.js` draws: the numbers of the tables, nothing more.

    A hidden median is `None` (JSON `null`) with its quartiles, so the chart
    leaves a gap there, and the page never carries the hidden numbers.
    """
    top = data.top_skills
    skills = top.skills if top else ()
    weeks = data.salaries
    return {
        "skills": {
            "jobs": top.jobs if top else 0,
            "labels": [skill.name for skill in skills],
            "shares": [skill.share for skill in skills],
            "n": [skill.n for skill in skills],
        },
        "salaries": {
            "labels": [_axis_label(week.week, week.partial) for week in weeks],
            "n": [week.n for week in weeks],
            "p25": [week.quartiles.p25 if week.quartiles else None for week in weeks],
            "median": [week.quartiles.median if week.quartiles else None for week in weeks],
            "p75": [week.quartiles.p75 if week.quartiles else None for week in weeks],
        },
        "jobs": {
            "labels": [_axis_label(row.week, row.total.partial) for row in jobs],
            "series": [
                {"name": "All sources", "values": [row.total.jobs for row in jobs]},
                *(
                    {"name": site.name, "values": [row.by_source[slug] for row in jobs]}
                    for slug, site in data.sources.items()
                ),
            ],
        },
    }


def day_label(day: date) -> str:
    """ "9 Nov 2026"."""
    return f"{day.day} {day:%b %Y}"


def _axis_label(week: date, partial: bool) -> str:
    """A week on a chart's axis, "9 Nov"; an asterisk marks one the data cover in part."""
    label = f"{week.day} {week:%b}"
    return f"{label}*" if partial else label


def _first_settling(weeks: list[tuple[date, bool]]) -> str | None:
    """The first week whose numbers may still change; every later one may too."""
    return next((day_label(week) for week, settling in weeks if settling), None)
