"""queue matched clusters per subscription for delivery to Telegram

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-08 20:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing subscriptions start now: nothing collected before this
    # migration is sent.
    op.add_column(
        "subscription",
        sa.Column(
            "active_since",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "notification",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("cluster_id", sa.BigInteger(), nullable=False),
        sa.Column("vacancy_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'skipped')",
            name=op.f("ck_notification_status"),
        ),
        sa.CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)", name=op.f("ck_notification_sent")
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscription.id"],
            name=op.f("fk_notification_subscription_id_subscription"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["vacancy_cluster.id"],
            name=op.f("fk_notification_cluster_id_vacancy_cluster"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancy.id"],
            name=op.f("fk_notification_vacancy_id_vacancy"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification")),
        sa.UniqueConstraint(
            "subscription_id",
            "cluster_id",
            name=op.f("uq_notification_subscription_id_cluster_id"),
        ),
    )
    op.create_index(op.f("ix_notification_cluster_id"), "notification", ["cluster_id"])
    op.create_index(op.f("ix_notification_vacancy_id"), "notification", ["vacancy_id"])
    op.create_index(
        "ix_notification_pending_next_attempt_at",
        "notification",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("notification")
    op.drop_column("subscription", "active_since")
