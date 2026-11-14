"""The vacancy search page, open to everyone.

`GET /vacancies` takes the search as query parameters, so a search is a link
that can be shared or bookmarked. The form works without JavaScript. With
HTMX it replaces only the results and pushes the new URL. The page keeps no
HTMX history snapshot, which would lose the form's state, so going back asks
for the whole page again (`HX-History-Restore-Request`) and the form is
rendered from the URL. An invalid form is shown again with its errors and
status 422. The source filter offers the sources of the `source` table, by
the same names every result links with.
"""

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse

from vacancy_radar.api.deps import DbSession, Now
from vacancy_radar.api.labels import (
    FORMAT_LABELS,
    LEVEL_LABELS,
    PERIOD_LABELS,
    SKILL_GROUPS,
    SORT_LABELS,
)
from vacancy_radar.api.templating import day_label, templates
from vacancy_radar.db.sources import source_sites
from vacancy_radar.domain.card import details, format_salary, place, skill_names
from vacancy_radar.domain.normalize import kyiv_date
from vacancy_radar.domain.search import (
    EMPTY_FORM,
    MAX_QUERY_LENGTH,
    InvalidSearchError,
    SearchForm,
    build_search,
)
from vacancy_radar.search.service import JobResult, search_jobs

router = APIRouter(tags=["search"])

# The response depends on these request headers, so a cache must tell them apart.
VARY: Final = "HX-Request, HX-History-Restore-Request"


def submitted_search(
    q: str = EMPTY_FORM.q,
    skills: Annotated[list[str] | None, Query()] = None,
    skills_match: str = EMPTY_FORM.skills_match,
    seniority: Annotated[list[str] | None, Query()] = None,
    work_formats: Annotated[list[str] | None, Query()] = None,
    min_salary_usd: str = EMPTY_FORM.min_salary_usd,
    period: str = EMPTY_FORM.period,
    sources: Annotated[list[str] | None, Query()] = None,
    sort: str = EMPTY_FORM.sort,
    page: str = EMPTY_FORM.page,
) -> SearchForm:
    """The form from the query string; a missing parameter takes `SearchForm`'s default."""
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
async def search_page(
    request: Request, session: DbSession, now: Now, form: Submitted
) -> HTMLResponse:
    sources = {slug: site.name for slug, site in (await source_sites(session)).items()}
    context: dict[str, Any] = {"form": form, "errors": {}, "page": None, "results": []}
    try:
        query = build_search(form, sources=tuple(sources))
    except InvalidSearchError as exc:
        context["errors"] = exc.errors
    else:
        page = await search_jobs(session, query, now=now)
        # Past the last page, "previous" goes to the last one, not to another empty page.
        previous = min(query.page - 1, page.pages)
        context |= {
            "page": page,
            "results": [_result_view(result) for result in page.results],
            "previous_url": _page_url(request, previous) if previous >= 1 else None,
            "next_url": _page_url(request, query.page + 1) if query.page < page.pages else None,
        }

    only_results = (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )
    response = templates.TemplateResponse(
        request,
        "search/_results.html" if only_results else "search/page.html",
        context | _form_context(form, sources),
        status_code=(
            status.HTTP_422_UNPROCESSABLE_CONTENT if context["errors"] else status.HTTP_200_OK
        ),
    )
    response.headers["Vary"] = VARY
    return response


def _form_context(form: SearchForm, sources: dict[str, str]) -> dict[str, Any]:
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
        "source_labels": sources,
        "periods": PERIOD_LABELS,
        "sorts": SORT_LABELS,
    }


def _result_view(result: JobResult) -> dict[str, Any]:
    """What the results template shows for one job, formatted."""
    card = result.card
    seen = kyiv_date(result.first_seen_at)
    return {
        "title": card.title,
        "place": place(card.company, card.cities),
        "salary": format_salary(card),
        "details": " · ".join(details(card)),
        "skills": ", ".join(skill_names(card)),
        "snippet_html": result.snippet_html,
        "first_seen": day_label(seen),
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
