"""The market analytics page, open to everyone.

`GET /analytics?month=&level=` shows three aggregates of `read_analytics`:
top skills of a month, weekly salary quartiles of a level, and new jobs per
week. Each chart has a table with the same numbers, so the page works and
reads without JavaScript. The charts are drawn by `static/analytics.js` from
a JSON block in the page (`ChartData`); the page has no inline script, so a
Content Security Policy can forbid inline scripts later.

The choices are a plain GET form sent by its button: a link that can be
shared, and Back restores both the page and the form. It is not sent on
`change`, because a keyboard changes a closed select on every arrow key.
A hidden median is never sent, neither in a table nor in the JSON, so no
one can read it from the page source.
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, TypedDict

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse

from vacancy_radar.analytics.service import Analytics, JobsWeek, read_analytics
from vacancy_radar.api.deps import DbSession, Now
from vacancy_radar.api.labels import LEVEL_LABELS
from vacancy_radar.api.templating import day_label, month_label, templates
from vacancy_radar.db.sources import SourceSite, source_sites
from vacancy_radar.domain.analytics import (
    EMPTY_ANALYTICS_FORM,
    MIN_SAMPLE,
    AnalyticsForm,
    InvalidAnalyticsError,
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


# The JSON block that `static/analytics.js` reads. Every list of a chart has
# one item per row of its table, in the same order.


class SkillsChart(TypedDict):
    # Jobs of the month that name a skill: the base of every share.
    jobs: int
    labels: list[str]
    shares: list[float]
    n: list[int]


class SalariesChart(TypedDict):
    labels: list[str]
    n: list[int]
    # None for a week below `MIN_SAMPLE`: the chart leaves a gap there.
    p25: list[int | None]
    median: list[int | None]
    p75: list[int | None]


class JobsSeries(TypedDict):
    # "total" or a source slug: the script colours a series by it, not by position.
    key: str
    name: str
    values: list[int]


class JobsChart(TypedDict):
    labels: list[str]
    series: list[JobsSeries]


class ChartData(TypedDict):
    skills: SkillsChart
    salaries: SalariesChart
    jobs: JobsChart


@dataclass(frozen=True, slots=True)
class JobsRow:
    total: JobsWeek
    # Jobs that week by source slug, 0 for a source without any.
    by_source: dict[str, int]


@router.get("/analytics", response_class=HTMLResponse, name="analytics")
async def analytics_page(
    request: Request, session: DbSession, now: Now, form: Submitted
) -> HTMLResponse:
    context: dict[str, Any] = {"form": form, "errors": {}, "data": None}
    try:
        query = build_analytics_query(form)
    except InvalidAnalyticsError as exc:
        context["errors"] = exc.errors
    else:
        data = await read_analytics(session, now, month=query.month, level=query.level)
        if data is not None:
            context |= _page_context(data, await source_sites(session))
    return templates.TemplateResponse(
        request,
        "analytics/page.html",
        context,
        status_code=(
            status.HTTP_422_UNPROCESSABLE_CONTENT if context["errors"] else status.HTTP_200_OK
        ),
    )


def _page_context(data: Analytics, sources: dict[str, SourceSite]) -> dict[str, Any]:
    summary = data.summary
    refreshed = summary.refreshed_at.astimezone(KYIV)
    jobs = _jobs_rows(data, sources)
    return {
        "data": data,
        "sources": sources,
        "levels": LEVEL_LABELS,
        "min_sample": MIN_SAMPLE,
        "month_options": _month_options(data),
        "chosen_month": month_param(data.month) if data.month else "",
        "level_label": LEVEL_LABELS[data.level] if data.level else "Every level",
        "refreshed": f"{day_label(refreshed.date())}, {refreshed:%H:%M} Kyiv time",
        "salary_share": share(summary.with_salary, summary.with_skills),
        "jobs": jobs,
        "salaries_settle_from": _first_settling(
            [(week.week, week.settling) for week in data.salaries]
        ),
        "jobs_settle_from": _first_settling([(row.total.week, row.total.settling) for row in jobs]),
        "week_label": week_label,
        "month_heading": month_heading,
        "chart_data": _chart_data(data, sources, jobs),
    }


def week_label(week: date, partial: bool, *, year: bool = True) -> str:
    """A week in a table ("9 Nov 2026") or on an axis ("9 Nov").

    An asterisk marks a week the data cover only in part; this is the one
    place that adds it, for tables and charts alike.
    """
    return _mark_partial(day_label(week, year=year), partial)


def month_heading(month: date, partial: bool) -> str:
    """ "November 2026", with the same asterisk as `week_label`."""
    return _mark_partial(month_label(month), partial)


def _mark_partial(label: str, partial: bool) -> str:
    return f"{label}*" if partial else label


def _month_options(data: Analytics) -> dict[str, str]:
    """The months to choose from, newest first, the month shown among them.

    A month asked for without data is offered too, so the form shows the
    month the heading names, and sending the form again keeps it.
    """
    months = set(data.months) | ({data.month} if data.month else set())
    return {month_param(month): month_label(month) for month in sorted(months, reverse=True)}


def _jobs_rows(data: Analytics, sources: dict[str, SourceSite]) -> list[JobsRow]:
    """A row per week of the total, which has every week any source has."""
    by_source = {
        slug: {week.week: week.jobs for week in weeks}
        for slug, weeks in data.jobs_by_source.items()
    }
    return [
        JobsRow(
            total=total,
            by_source={slug: by_source.get(slug, {}).get(total.week, 0) for slug in sources},
        )
        for total in data.jobs_total
    ]


def _chart_data(data: Analytics, sources: dict[str, SourceSite], jobs: list[JobsRow]) -> ChartData:
    """The tables' rows as columns, for `static/analytics.js`."""
    skills = data.top_skills.skills if data.top_skills else ()
    salaries = data.salaries
    return {
        "skills": {
            "jobs": data.top_skills.jobs if data.top_skills else 0,
            "labels": [skill.name for skill in skills],
            "shares": [skill.share for skill in skills],
            "n": [skill.n for skill in skills],
        },
        "salaries": {
            "labels": [week_label(week.week, week.partial, year=False) for week in salaries],
            "n": [week.n for week in salaries],
            "p25": [week.quartiles.p25 if week.quartiles else None for week in salaries],
            "median": [week.quartiles.median if week.quartiles else None for week in salaries],
            "p75": [week.quartiles.p75 if week.quartiles else None for week in salaries],
        },
        "jobs": {
            "labels": [week_label(row.total.week, row.total.partial, year=False) for row in jobs],
            "series": [
                {
                    "key": "total",
                    "name": "All sources",
                    "values": [row.total.jobs for row in jobs],
                },
                *(
                    JobsSeries(
                        key=slug, name=site.name, values=[row.by_source[slug] for row in jobs]
                    )
                    for slug, site in sources.items()
                ),
            ],
        },
    }


def _first_settling(weeks: list[tuple[date, bool]]) -> str | None:
    """The first week whose numbers may still change; every later one may too."""
    return next((day_label(week) for week, settling in weeks if settling), None)
