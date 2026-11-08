"""The lifecycle of a delivery: one Telegram message about one cluster to one account.

A subscription that matches a cluster is a *match* (`notification`); the
message it causes is a *delivery*, one per account and cluster however many
of the account's subscriptions matched. Keying deliveries by account is what
keeps a second matching subscription from sending the same vacancy again.
"""

from typing import Final, Literal

DeliveryStatus = Literal["pending", "sending", "sent", "failed", "skipped"]

# pending: waits for `next_attempt_at`.
# sending: claimed right before the request; stays so only if the worker died.
# sent:    delivered.
# failed:  given up, with the reason in `error`.
# skipped: the chat was unlinked before the message went out.
DELIVERY_STATUSES: Final[tuple[DeliveryStatus, ...]] = (
    "pending",
    "sending",
    "sent",
    "failed",
    "skipped",
)
