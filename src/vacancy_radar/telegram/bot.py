"""What the bot answers to a message. No Bot API calls: the caller sends the reply.

Only private chats are served, since matches are personal. Group chats,
channels and messages without text get no reply. The commands:

- `/start <token>` links the chat to the account the token was issued for;
- `/stop` unlinks it;
- anything else, `/start` without a token included, gets a short help.

Replies are HTML, the parse mode `TelegramClient.send_message` uses, with
every inserted value escaped.
"""

from datetime import datetime
from html import escape

from sqlalchemy.orm import Session

from vacancy_radar.telegram.client import Update
from vacancy_radar.telegram.linking import link_chat, unlink_chat


def handle_update(session: Session, update: Update, *, now: datetime, site_url: str) -> str | None:
    """The reply to send back to the update's chat, or None to stay silent."""
    message = update.message
    if message is None or message.chat.type != "private" or message.text is None:
        return None
    command, _, argument = message.text.strip().partition(" ")
    # In a chat with several bots, commands arrive as `/start@SomeBot`.
    command = command.split("@", 1)[0].lower()
    argument = argument.strip()
    page = escape(f"{site_url}/telegram")

    if command == "/start" and argument:
        linked = link_chat(session, argument, message.chat.id, now=now)
        if linked is None:
            return (
                "This link has expired or was already used. "
                f"Get a new one on {page} and press Start again."
            )
        moved = (
            "It was linked to another account until now, which gets nothing here any more.\n\n"
            if linked.moved_from_another_account
            else ""
        )
        return (
            f"This chat is now linked to <b>{escape(linked.email)}</b>. "
            "New vacancies that match your active subscriptions will arrive here.\n\n"
            f"{moved}Send /stop to unlink it."
        )
    if command == "/stop":
        if unlink_chat(session, message.chat.id, via="bot"):
            return (
                "This chat is unlinked, and nothing more will be sent here. "
                f"Link it again on {page}."
            )
        return f"This chat is not linked to an account. Link it on {page}."
    return (
        "<b>Vacancy Radar</b> sends new IT vacancies that match your subscriptions.\n\n"
        f"To receive them here, sign in on {page} and press <i>Link Telegram</i>.\n"
        "/stop unlinks this chat."
    )
