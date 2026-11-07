"""link accounts to Telegram chats

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "app_user", sa.Column("telegram_linked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        op.f("uq_app_user_telegram_chat_id"), "app_user", ["telegram_chat_id"]
    )
    op.create_check_constraint(
        op.f("ck_app_user_telegram_linked"),
        "app_user",
        "(telegram_chat_id IS NULL) = (telegram_linked_at IS NULL)",
    )
    op.create_table(
        "telegram_link_token",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_telegram_link_token_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_telegram_link_token")),
        sa.UniqueConstraint("user_id", name=op.f("uq_telegram_link_token_user_id")),
    )


def downgrade() -> None:
    op.drop_table("telegram_link_token")
    op.drop_constraint(op.f("ck_app_user_telegram_linked"), "app_user", type_="check")
    op.drop_constraint(op.f("uq_app_user_telegram_chat_id"), "app_user", type_="unique")
    op.drop_column("app_user", "telegram_linked_at")
    op.drop_column("app_user", "telegram_chat_id")
