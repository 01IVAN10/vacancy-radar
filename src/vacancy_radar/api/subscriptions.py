"""Subscription pages: list, create, edit, pause or resume, delete.

Every route needs a signed-in user and only ever sees that user's
subscriptions; another account's id gives 404, the same as a missing one.
The forms work without JavaScript. With HTMX, pausing and deleting replace
the list in place, limit notice and empty state included.
"""

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from vacancy_radar.api.deps import CurrentUser, DbSession, require_user
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import verify_csrf
from vacancy_radar.domain.seniority import LEVELS
from vacancy_radar.domain.skills import SKILLS, SKILLS_BY_SLUG, Category
from vacancy_radar.domain.subscriptions import (
    InvalidSubscriptionError,
    SubscriptionFilter,
    SubscriptionForm,
    build_subscription,
)
from vacancy_radar.domain.work_format import WORK_FORMATS
from vacancy_radar.subscriptions import service
from vacancy_radar.subscriptions.service import (
    MAX_SUBSCRIPTIONS,
    SavedSubscription,
    SubscriptionLimitError,
)
from vacancy_radar.telegram import linking

# Router-level, so no route can be added without it. It runs before each
# route's own dependencies: a signed-out POST goes to sign-in, not to 403.
router = APIRouter(
    prefix="/subscriptions", tags=["subscriptions"], dependencies=[Depends(require_user)]
)

LIMIT_REACHED: Final = f"You have reached the limit of {MAX_SUBSCRIPTIONS} subscriptions."
NOT_FOUND: Final = "No such subscription."

CATEGORY_LABELS: Final[dict[Category, str]] = {
    "language": "Languages",
    "backend": "Backend",
    "frontend": "Frontend",
    "mobile": "Mobile",
    "database": "Databases",
    "data": "Data",
    "ml": "Machine learning",
    "cloud": "Cloud",
    "devops": "DevOps",
    "qa": "QA",
    "embedded": "Embedded",
    "tool": "Tools",
}
LEVEL_LABELS: Final = {level: level.capitalize() for level in LEVELS}
FORMAT_LABELS: Final = {work_format: work_format.capitalize() for work_format in WORK_FORMATS}
# Skills by category, in dictionary order, for the form's checkbox groups.
SKILL_GROUPS: Final = [
    (label, [skill for skill in SKILLS if skill.category == category])
    for category, label in CATEGORY_LABELS.items()
]


def submitted_form(
    name: Annotated[str, Form()] = "",
    skills: Annotated[list[str] | None, Form()] = None,
    skills_match: Annotated[str, Form()] = "any",
    seniority: Annotated[list[str] | None, Form()] = None,
    work_formats: Annotated[list[str] | None, Form()] = None,
    min_salary_usd: Annotated[str, Form()] = "",
    # A checkbox is sent only when it is ticked.
    include_unsalaried: Annotated[str | None, Form()] = None,
) -> SubscriptionForm:
    return SubscriptionForm(
        name=name,
        skills=frozenset(skills or ()),
        skills_match=skills_match,
        seniority=frozenset(seniority or ()),
        work_formats=frozenset(work_formats or ()),
        min_salary_usd=min_salary_usd,
        include_unsalaried=include_unsalaried is not None,
    )


Submitted = Annotated[SubscriptionForm, Depends(submitted_form)]


@router.get("", response_class=HTMLResponse, name="subscriptions_list")
async def list_page(request: Request, session: DbSession, user: CurrentUser) -> HTMLResponse:
    context = await _list_context(session, user.id)
    context["telegram_linked"] = await linking.linked_at(session, user.id) is not None
    return templates.TemplateResponse(request, "subscriptions/list.html", context)


@router.get("/new", response_class=HTMLResponse, name="subscriptions_new")
async def new_page(request: Request, session: DbSession, user: CurrentUser) -> HTMLResponse:
    if await service.count_for_user(session, user.id) >= MAX_SUBSCRIPTIONS:
        # Say so before the form is filled in, not after it is sent.
        return templates.TemplateResponse(
            request, "subscriptions/limit.html", {"message": LIMIT_REACHED}
        )
    return _form_page(request, SubscriptionForm(), editing=None)


@router.post(
    "",
    dependencies=[Depends(verify_csrf)],
    response_class=HTMLResponse,
    name="subscriptions_create",
)
async def create(
    request: Request, session: DbSession, user: CurrentUser, form: Submitted
) -> Response:
    try:
        await service.create(session, user.id, build_subscription(form))
    except InvalidSubscriptionError as exc:
        return _form_page(request, form, editing=None, errors=exc.errors)
    except SubscriptionLimitError:
        return _form_page(request, form, editing=None, notice=LIMIT_REACHED)
    return _to_list(request)


@router.get("/{subscription_id}/edit", response_class=HTMLResponse, name="subscriptions_edit")
async def edit_page(
    request: Request, session: DbSession, user: CurrentUser, subscription_id: int
) -> HTMLResponse:
    saved = _found(await service.get_for_user(session, user.id, subscription_id))
    return _form_page(request, SubscriptionForm.of(saved.name, saved.filter), editing=saved.id)


@router.post(
    "/{subscription_id}",
    dependencies=[Depends(verify_csrf)],
    response_class=HTMLResponse,
    name="subscriptions_update",
)
async def update(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    form: Submitted,
    subscription_id: int,
) -> Response:
    try:
        draft = build_subscription(form)
    except InvalidSubscriptionError as exc:
        # Check ownership first, so another account's id is 404 even with a bad form.
        _found(await service.get_for_user(session, user.id, subscription_id))
        return _form_page(request, form, editing=subscription_id, errors=exc.errors)
    _found(await service.update_for_user(session, user.id, subscription_id, draft))
    return _to_list(request)


@router.post(
    "/{subscription_id}/toggle",
    dependencies=[Depends(verify_csrf)],
    response_class=HTMLResponse,
    name="subscriptions_toggle",
)
async def toggle(
    request: Request, session: DbSession, user: CurrentUser, subscription_id: int
) -> Response:
    _found(await service.toggle_active(session, user.id, subscription_id))
    return await _changed(request, session, user.id)


@router.post(
    "/{subscription_id}/delete",
    dependencies=[Depends(verify_csrf)],
    response_class=HTMLResponse,
    name="subscriptions_delete",
)
async def delete(
    request: Request, session: DbSession, user: CurrentUser, subscription_id: int
) -> Response:
    if not await service.delete_for_user(session, user.id, subscription_id):
        raise _not_found()
    return await _changed(request, session, user.id)


def describe(criteria: SubscriptionFilter) -> list[str]:
    """The filter's criteria as short phrases for the list."""
    parts: list[str] = []
    # A skill removed from the dictionary is shown by its slug; it matches nothing.
    skills = [
        SKILLS_BY_SLUG[slug].name if slug in SKILLS_BY_SLUG else f"{slug} (no longer tracked)"
        for slug in criteria.skills
    ]
    if len(skills) == 1:
        parts.append(skills[0])
    elif skills:
        parts.append(f"{criteria.skills_match} of {', '.join(skills)}")
    if criteria.seniority:
        parts.append(", ".join(LEVEL_LABELS[level] for level in criteria.seniority))
    if criteria.work_formats:
        parts.append(", ".join(FORMAT_LABELS[fmt] for fmt in criteria.work_formats))
    if criteria.salary is not None:
        floor = f"from ${criteria.salary.min_usd:,}/month"
        parts.append(f"{floor} or not stated" if criteria.salary.include_unsalaried else floor)
    return parts


async def _list_context(session: DbSession, user_id: int) -> dict[str, Any]:
    subscriptions = await service.list_for_user(session, user_id)
    return {
        "rows": [_row(saved) for saved in subscriptions],
        "at_limit": len(subscriptions) >= MAX_SUBSCRIPTIONS,
        "limit_message": LIMIT_REACHED,
    }


def _row(saved: SavedSubscription) -> dict[str, Any]:
    return {"subscription": saved, "criteria": describe(saved.filter)}


async def _changed(request: Request, session: DbSession, user_id: int) -> Response:
    """After a pause or delete: the refreshed list for HTMX, a redirect otherwise."""
    if request.headers.get("HX-Request") != "true":
        return _to_list(request)
    context = await _list_context(session, user_id)
    return templates.TemplateResponse(request, "subscriptions/_list.html", context)


def _form_page(
    request: Request,
    form: SubscriptionForm,
    *,
    editing: int | None,
    errors: dict[str, str] | None = None,
    notice: str | None = None,
) -> HTMLResponse:
    """The new-subscription form, or the edit form of subscription `editing`."""
    if editing is None:
        heading, action = "New subscription", request.url_for("subscriptions_create")
    else:
        heading, action = (
            "Edit subscription",
            request.url_for("subscriptions_update", subscription_id=editing),
        )
    return templates.TemplateResponse(
        request,
        "subscriptions/form.html",
        {
            "form": form,
            "heading": heading,
            "action": action.path,
            "errors": errors or {},
            "notice": notice,
            "skill_groups": SKILL_GROUPS,
            "levels": LEVEL_LABELS,
            "formats": FORMAT_LABELS,
        },
        status_code=(
            status.HTTP_422_UNPROCESSABLE_CONTENT if errors or notice else status.HTTP_200_OK
        ),
    )


def _to_list(request: Request) -> RedirectResponse:
    return RedirectResponse(request.url_for("subscriptions_list").path, status.HTTP_303_SEE_OTHER)


def _found(saved: SavedSubscription | None) -> SavedSubscription:
    if saved is None:
        raise _not_found()
    return saved


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
