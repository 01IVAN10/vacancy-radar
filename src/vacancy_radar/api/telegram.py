"""The Telegram page: link the account to a Telegram chat, or unlink it.

Linking shows a one-time deep link to the bot (`telegram/linking.py`). The
link is shown once, in the response to the POST that issued it, and never
stored in a URL the browser keeps in its history.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from vacancy_radar.api.deps import Config, CurrentUser, DbSession, require_user
from vacancy_radar.api.templating import templates
from vacancy_radar.auth.csrf import verify_csrf
from vacancy_radar.telegram import linking

# Router-level, as on the subscription pages: a signed-out POST goes to sign-in.
router = APIRouter(prefix="/telegram", tags=["telegram"], dependencies=[Depends(require_user)])

LINK_MINUTES = int(linking.LINK_TOKEN_TTL.total_seconds() // 60)


@router.get("", response_class=HTMLResponse, name="telegram_page")
async def page(
    request: Request, session: DbSession, user: CurrentUser, config: Config
) -> HTMLResponse:
    return await _page(request, session, user.id, config, link=None)


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
    deep_link = linking.deep_link(config.telegram_bot_username, token)
    return await _page(request, session, user.id, config, link=deep_link)


@router.post("/unlink", dependencies=[Depends(verify_csrf)], name="telegram_unlink")
async def unlink(request: Request, session: DbSession, user: CurrentUser) -> RedirectResponse:
    await linking.unlink_user(session, user.id)
    return RedirectResponse(request.url_for("telegram_page").path, status.HTTP_303_SEE_OTHER)


async def _page(
    request: Request, session: DbSession, user_id: int, config: Config, *, link: str | None
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "telegram/page.html",
        {
            "linked_at": await linking.linked_at(session, user_id),
            "configured": config.telegram_bot_username is not None,
            "bot_username": config.telegram_bot_username,
            "link": link,
            "link_minutes": LINK_MINUTES,
        },
    )
