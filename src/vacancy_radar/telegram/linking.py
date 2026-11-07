"""Linking an account to a Telegram chat with a one-time deep-link token.

1. The signed-in user asks for a link on `/telegram`. `issue_link_token`
   stores the hash of a new random token and the page shows
   `https://t.me/<bot>?start=<token>`.
2. Telegram opens the bot, and pressing Start sends `/start <token>` from
   the user's chat. The bot calls `link_chat`, which deletes the token and
   stores the chat on the account in one transaction.

The token is single-use because using it deletes its row
(`DELETE … RETURNING`): of two `/start`s with one token, only one gets the
row back. It expires after `LINK_TOKEN_TTL`, and only its hash is stored, so
a database dump holds no usable link.

The web app calls the async functions and the bot process the sync ones,
each on its own engine, as everywhere else in the project.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final
from urllib.parse import urlencode

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from vacancy_radar.db.models import TelegramLinkToken, User

LINK_TOKEN_TTL: Final = timedelta(minutes=15)
# 32 random bytes are 43 URL-safe characters, inside Telegram's limit of 64
# characters from [A-Za-z0-9_-] for a deep-link parameter.
TOKEN_BYTES: Final = 32

# The columns of an account without a chat. The CHECK `telegram_linked` makes
# both NULL or neither, so every unlink writes them through this one value.
UNLINKED: Final = MappingProxyType({"telegram_chat_id": None, "telegram_linked_at": None})


@dataclass(frozen=True, slots=True)
class Linked:
    """The account a chat was just linked to."""

    email: str
    # The chat was linked to another account until now, which no longer gets matches here.
    moved_from_another_account: bool


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """What is stored instead of the token: its SHA-256 in hex.

    A plain hash suffices: the token is random and long, so it cannot be
    guessed from its hash, unlike a password.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def deep_link(bot_username: str, token: str) -> str:
    """The link that opens the bot and makes Start send `/start <token>`."""
    return f"https://t.me/{bot_username}?{urlencode({'start': token})}"


# --- web app (async) ----------------------------------------------------------


async def issue_link_token(session: AsyncSession, user_id: int, *, now: datetime) -> str:
    """Store a new token for the account, replacing any earlier one, and commit."""
    token = new_token()
    values = {"token_hash": hash_token(token), "expires_at": now + LINK_TOKEN_TTL}
    await session.execute(
        insert(TelegramLinkToken)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[TelegramLinkToken.user_id], set_=values)
    )
    await session.commit()
    return token


async def unlink_user(session: AsyncSession, user_id: int) -> None:
    """Forget the account's chat, and commit. Nothing more is sent there."""
    await session.execute(update(User).where(User.id == user_id).values(**UNLINKED))
    await session.commit()


# --- bot (sync) ---------------------------------------------------------------


def link_chat(session: Session, token: str, chat_id: int, *, now: datetime) -> Linked | None:
    """Use the token to link `chat_id` to its account, and commit.

    Returns the account, or None for an unknown, used or expired token. A
    chat linked to another account moves to this one: whoever sent the token
    controls the chat. A chat the account had before is replaced.
    """
    used = session.execute(
        delete(TelegramLinkToken)
        .where(TelegramLinkToken.token_hash == hash_token(token))
        .returning(TelegramLinkToken.user_id, TelegramLinkToken.expires_at)
    ).one_or_none()
    if used is None or used.expires_at <= now:
        # An expired token is deleted all the same.
        session.commit()
        return None

    # Free the chat first: it is unique, and checked statement by statement.
    previous_owner = session.scalar(
        update(User)
        .where(User.telegram_chat_id == chat_id, User.id != used.user_id)
        .values(**UNLINKED)
        .returning(User.id)
    )
    # The token's row referenced the account (ON DELETE CASCADE), so it exists.
    email = session.execute(
        update(User)
        .where(User.id == used.user_id)
        .values(telegram_chat_id=chat_id, telegram_linked_at=now)
        .returning(User.email)
    ).scalar_one()
    session.commit()
    return Linked(email=email, moved_from_another_account=previous_owner is not None)


def unlink_chat(session: Session, chat_id: int) -> bool:
    """Forget the chat on whichever account has it, and commit. False if none had it."""
    unlinked = session.scalar(
        update(User).where(User.telegram_chat_id == chat_id).values(**UNLINKED).returning(User.id)
    )
    session.commit()
    return unlinked is not None
