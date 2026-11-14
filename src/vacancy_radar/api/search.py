"""The vacancy search page, open to everyone.

`GET /vacancies` takes the search as query parameters, so a search is a link
that can be shared or bookmarked. The form works without JavaScript. With
HTMX it replaces only the results and pushes the new URL. The page keeps no
HTMX history snapshot, which would lose the form's state, so going back asks
for the whole page again (`HX-History-Restore-Request`) and the form is
rendered from the URL. An invalid form is
shown again with its errors and status 422.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse

from vacancy_radar.api.deps import DbSession
from vacancy_radar.api.labels import FORMAT_LABELS, LEVEL_LABELS, SKILL_GROUPS, SOURCE_LABELS
from vacancy_radar.api.templating import templates
from vacancy_radar.domain.card import details, format_salary, skill_names
from vacancy_radar.domain.normalize import kyiv_date
from vacancy_radar.domain.search import (
    MAX_QUERY_LENGTH,
    PERIOD_DAYS,
    InvalidSearchError,
    SearchForm,
    build_search,
)
from vacancy_radar.search.service import JobResult, search_jobs

router = APIRouter(tags=["search"])

PERIOD_LABELS = {
    str(days): "Last 24 hours" if days == 1 else f"Last {days} days" for days in PERIOD_DAYS
}
SORT_LABELS = {"relevance": "Most relevant", "newest": "Newest"}


def submitted_search(
    q: str = "",
    skills: Annotated[list[str] | None, Query()] = None,
    skills_match: str = "any",
    seniority: Annotated[list[str] | None, Query()] = None,
    work_formats: Annotated[list[str] | None, Query()] = None,
    min_salary_usd: str = "",
    period: str = "",
    sources: Annotated[list[str] | None, Query()] = None,
    sort: str = "",
    page: str = "1",
) -> SearchForm:
    return SearchForm(
        q=q,
        skills=frozenset(skills or ()),
        skills_match=skills_match,
        seniority=frozenset(seniority or ()),
        work_formats=frozenset(work_formats or ()),
        min_salary_usd=min_salary_usd,
        period=period,
        sources=frozenset(sources or ()),
        sort=sort,
        page=page,
    )


Submitted = Annotated[SearchForm, Depends(submitted_search)]


@router.get("/vacancies", response_class=HTMLResponse, name="vacancies")
async def search_page(request: Request, session: DbSession, form: Submitted) -> HTMLResponse:
    context: dict[str, Any] = {"form": form, "errors": {}, "page": None, "results": []}
    try:
        query = build_search(form, sources=tuple(SOURCE_LABELS))
    except InvalidSearchError as exc:
        context["errors"] = exc.errors
    else:
        page = await search_jobs(session, query, now=datetime.now(UTC))
        context |= {
            "page": page,
            "results": [_result_view(result) for result in page.results],
            "previous_url": _page_url(request, query.page - 1) if query.page > 1 else None,
            "next_url": _page_url(request, query.page + 1) if query.page < page.pages else None,
        }

    only_results = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    response = templates.TemplateResponse(
        request,
        "search/_results.html" if only_results else "search/page.html",
        context | _form_context(form),
        status_code=(
            status.HTTP_422_UNPROCESSABLE_CONTENT if context["errors"] else status.HTTP_200_OK
        ),
    )
    # The same URL answers with a page or a fragment, so caches must tell them apart.
    response.headers["Vary"] = "HX-Request"
    return response


def _form_context(form: SearchForm) -> dict[str, Any]:
    filters_used = bool(
        form.skills
        or form.seniority
        or form.work_formats
        or form.min_salary_usd
        or form.period
        or form.sources
    )
    return {
        "filters_used": filters_used,
        "max_query_length": MAX_QUERY_LENGTH,
        "skill_groups": SKILL_GROUPS,
        "levels": LEVEL_LABELS,
        "formats": FORMAT_LABELS,
        "source_labels": SOURCE_LABELS,
        "periods": PERIOD_LABELS,
        "sorts": SORT_LABELS,
    }


def _result_view(result: JobResult) -> dict[str, Any]:
    """What the results template shows for one job, formatted."""
    card = result.card
    seen = kyiv_date(result.first_seen_at)
    return {
        "title": card.title,
        "place": ", ".join([card.company, *card.cities] if card.company else card.cities),
        "salary": format_salary(card),
        "details": " · ".join(details(card)),
        "skills": ", ".join(skill_names(card)),
        "snippet_html": result.snippet_html,
        "first_seen": f"{seen.day} {seen:%b %Y}",
        # Only web links: a feed must not be able to put a `javascript:` URL on the page.
        "postings": [
            posting
            for posting in result.postings
            if posting.url.startswith(("https://", "http://"))
        ],
    }


def _page_url(request: Request, page: int) -> str:
    url = request.url.include_query_params(page=page)
    return f"{url.path}?{url.query}"
