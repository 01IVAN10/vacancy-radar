"""Subscription pages: list, create, edit, pause or resume, delete.

Every route needs a signed-in user and only ever sees that user's
subscriptions; another account's id gives 404, the same as a missing one.
The forms work without JavaScript. With HTMX, pausing and deleting replace
only the subscription's table row instead of reloading the page.
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from vacancy_radar.api.deps import CurrentUser, DbSession, require_user
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import verify_csrf
from vacancy_radar.db.models import Subscription
from vacancy_radar.domain.seniority import LEVELS
from vacancy_radar.domain.skills import SKILLS, SKILLS_BY_SLUG
from vacancy_radar.domain.subscriptions import (
    InvalidSubscriptionError,
    SubscriptionDraft,
    build_subscription,
)
from vacancy_radar.domain.work_format import WORK_FORMATS
from vacancy_radar.subscriptions import service
from vacancy_radar.subscriptions.service import MAX_SUBSCRIPTIONS, SubscriptionLimitError

# Router-level, so no route can be added without it. It runs before each
# route's own dependencies: a signed-out POST goes to sign-in, not to 403.
router = APIRouter(
    prefix="/subscriptions", tags=["subscriptions"], dependencies=[Depends(require_user)]
)

LIST_URL: Final = "/subscriptions"
LIMIT_REACHED: Final = f"You can have at most {MAX_SUBSCRIPTIONS} subscriptions."

CATEGORY_LABELS: Final = {
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
# Keyed by `str`: stored rows hold plain strings, which the CHECKs keep to these values.
LEVEL_LABELS: Final[dict[str, str]] = {level: level.capitalize() for level in LEVELS}
FORMAT_LABELS: Final[dict[str, str]] = {fmt: fmt.capitalize() for fmt in WORK_FORMATS}
# Skills by category, in dictionary order, for the form's checkbox groups.
SKILL_GROUPS: Final = [
    (label, [skill for skill in SKILLS if skill.category == category])
    for category, label in CATEGORY_LABELS.items()
]


@dataclass
class SubscriptionForm:
    """The form's values as the user entered them, to fill the form again."""

    name: str = ""
    skills: set[str] = field(default_factory=set)
    skills_match: str = "any"
    seniority: set[str] = field(default_factory=set)
    work_formats: set[str] = field(default_factory=set)
    min_salary_usd: str = ""
    include_unsalaried: bool = True

    @classmethod
    def of(cls, subscription: Subscription) -> "SubscriptionForm":
        salary = subscription.min_salary_usd
        return cls(
            name=subscription.name,
            skills=set(subscription.skills),
            skills_match=subscription.skills_match,
            seniority=set(subscription.seniority),
            work_formats=set(subscription.work_formats),
            min_salary_usd="" if salary is None else str(salary),
            include_unsalaried=subscription.include_unsalaried,
        )


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
        skills=set(skills or ()),
        skills_match=skills_match,
        seniority=set(seniority or ()),
        work_formats=set(work_formats or ()),
        min_salary_usd=min_salary_usd,
        include_unsalaried=include_unsalaried is not None,
    )


Submitted = Annotated[SubscriptionForm, Depends(submitted_form)]


@router.get("", response_class=HTMLResponse)
async def list_page(request: Request, session: DbSession, user: CurrentUser) -> HTMLResponse:
    subscriptions = await service.list_for_user(session, user.id)
    return templates.TemplateResponse(
        request,
        "subscriptions/list.html",
        {
            "rows": [_row(subscription) for subscription in subscriptions],
            "at_limit": len(subscriptions) >= MAX_SUBSCRIPTIONS,
            "limit": MAX_SUBSCRIPTIONS,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_page(request: Request) -> HTMLResponse:
    return _form_page(request, SubscriptionForm(), action=LIST_URL)


@router.post("", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
async def create(
    request: Request, session: DbSession, user: CurrentUser, form: Submitted
) -> Response:
    try:
        await service.create(session, user.id, _draft(form))
    except InvalidSubscriptionError as exc:
        return _form_page(request, form, action=LIST_URL, errors=exc.errors)
    except SubscriptionLimitError:
        return _form_page(request, form, action=LIST_URL, errors={"filter": LIMIT_REACHED})
    return RedirectResponse(LIST_URL, status.HTTP_303_SEE_OTHER)


@router.get("/{subscription_id}/edit", response_class=HTMLResponse)
async def edit_page(
    request: Request, session: DbSession, user: CurrentUser, subscription_id: int
) -> HTMLResponse:
    subscription = _found(await service.get_for_user(session, user.id, subscription_id))
    return _form_page(
        request, SubscriptionForm.of(subscription), action=f"{LIST_URL}/{subscription_id}"
    )


@router.post("/{subscription_id}", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse)
async def update(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    form: Submitted,
    subscription_id: int,
) -> Response:
    try:
        draft = _draft(form)
    except InvalidSubscriptionError as exc:
        # Check ownership first, so another account's id is 404 even with a bad form.
        _found(await service.get_for_user(session, user.id, subscription_id))
        return _form_page(request, form, action=f"{LIST_URL}/{subscription_id}", errors=exc.errors)
    _found(await service.update_for_user(session, user.id, subscription_id, draft))
    return RedirectResponse(LIST_URL, status.HTTP_303_SEE_OTHER)


@router.post(
    "/{subscription_id}/toggle", dependencies=[Depends(verify_csrf)], response_class=HTMLResponse
)
async def toggle(
    request: Request, session: DbSession, user: CurrentUser, subscription_id: int
) -> Response:
    subscription = _found(await service.toggle_active(session, user.id, subscription_id))
    if _is_htmx(request):
        return templates.TemplateResponse(
            request, "subscriptions/_row.html", {"row": _row(subscription)}
        )
    return RedirectResponse(LIST_URL, status.HTTP_303_SEE_OTHER)


@router.post("/{subscription_id}/delete", dependencies=[Depends(verify_csrf)])
async def delete(
    request: Request, session: DbSession, user: CurrentUser, subscription_id: int
) -> Response:
    if not await service.delete_for_user(session, user.id, subscription_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such subscription.")
    if _is_htmx(request):
        # An empty body: HTMX swaps the row out for nothing.
        return HTMLResponse("")
    return RedirectResponse(LIST_URL, status.HTTP_303_SEE_OTHER)


def describe(subscription: Subscription) -> list[str]:
    """The subscription's criteria as short phrases for the list."""
    parts: list[str] = []
    # A slug removed from the dictionary after the subscription was saved is skipped.
    skills = [SKILLS_BY_SLUG[slug].name for slug in subscription.skills if slug in SKILLS_BY_SLUG]
    if len(skills) == 1:
        parts.append(skills[0])
    elif skills:
        quantifier = "all of" if subscription.skills_match == "all" else "any of"
        parts.append(f"{quantifier} {', '.join(skills)}")
    if subscription.seniority:
        parts.append(", ".join(LEVEL_LABELS[level] for level in subscription.seniority))
    if subscription.work_formats:
        parts.append(", ".join(FORMAT_LABELS[fmt] for fmt in subscription.work_formats))
    if subscription.min_salary_usd is not None:
        salary = f"from ${subscription.min_salary_usd:,}/month"
        parts.append(f"{salary} or not stated" if subscription.include_unsalaried else salary)
    return parts


def _row(subscription: Subscription) -> dict[str, Any]:
    return {"subscription": subscription, "criteria": describe(subscription)}


def _draft(form: SubscriptionForm) -> SubscriptionDraft:
    return build_subscription(
        name=form.name,
        skills=sorted(form.skills),
        skills_match=form.skills_match,
        seniority=sorted(form.seniority),
        work_formats=sorted(form.work_formats),
        min_salary_usd=form.min_salary_usd,
        include_unsalaried=form.include_unsalaried,
    )


def _form_page(
    request: Request,
    form: SubscriptionForm,
    *,
    action: str,
    errors: dict[str, str] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "subscriptions/form.html",
        {
            "form": form,
            "action": action,
            "heading": "New subscription" if action == LIST_URL else "Edit subscription",
            "errors": errors or {},
            "skill_groups": SKILL_GROUPS,
            "levels": LEVEL_LABELS,
            "formats": FORMAT_LABELS,
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT if errors else status.HTTP_200_OK,
    )


def _found(subscription: Subscription | None) -> Subscription:
    if subscription is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such subscription.")
    return subscription


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"
