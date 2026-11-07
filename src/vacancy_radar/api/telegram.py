"""The Telegram page: link the account to a Telegram chat, or unlink it.

Linking shows a one-time deep link to the bot (`telegram/linking.py`). The
link is shown once, in the response to the POST that issued it, and never
stored in a URL the browser keeps in its history. That page points back to
`GET /telegram` for the result, because reloading it would send the POST
again and replace the token the user may be about to press Start with.
"""

from datetime import UTC, datetime
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from vacancy_radar.api.deps import Config, CurrentUser, DbSession, WebConfig, require_user
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import verify_csrf
from vacancy_radar.auth.session import SignedInUser
from vacancy_radar.telegram import linking

# Router-level, as on the subscription pages: a signed-out POST goes to sign-in.
router = APIRouter(prefix="/telegram", tags=["telegram"], dependencies=[Depends(require_user)])

LINK_MINUTES: Final = int(linking.LINK_TOKEN_TTL.total_seconds() // 60)


@router.get("", response_class=HTMLResponse, name="telegram_page")
async def page(request: Request, user: CurrentUser, config: Config) -> HTMLResponse:
    return _page(request, user, config, link=None)


@router.post(
    "/link",
    dependencies=[Depends(verify_csrf)],
    response_class=HTMLResponse,
    name="telegram_link",
)
async def link(
    request: Request, session: DbSession, user: CurrentUser, config: Config
) -> HTMLResponse:
    if config.telegram_bot_username is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram is not set up.")
    token = await linking.issue_link_token(session, user.id, now=datetime.now(UTC))
    return _page(request, user, config, link=linking.deep_link(config.telegram_bot_username, token))


@router.post("/unlink", dependencies=[Depends(verify_csrf)], name="telegram_unlink")
async def unlink(request: Request, session: DbSession, user: CurrentUser) -> RedirectResponse:
    await linking.unlink_user(session, user.id)
    return RedirectResponse(request.url_for("telegram_page").path, status.HTTP_303_SEE_OTHER)


def _page(
    request: Request, user: SignedInUser, config: WebConfig, *, link: str | None
) -> HTMLResponse:
    linked_at = user.telegram_linked_at
    return templates.TemplateResponse(
        request,
        "telegram/page.html",
        {
            # Shown in UTC whatever the database session's time zone is.
            "linked_at": None if linked_at is None else linked_at.astimezone(UTC),
            "configured": config.telegram_bot_username is not None,
            "bot_username": config.telegram_bot_username,
            "link": link,
            "link_minutes": LINK_MINUTES,
        },
    )
