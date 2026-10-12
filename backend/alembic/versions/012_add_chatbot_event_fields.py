"""add chatbot event fields to contacts and messages

Revision ID: 012
Revises: 011
Create Date: 2026-10-12

Schema for phase 1 of ai_chatbot_demo -> CRM conversation events
(docs/chatbot-events.md, section 6). Columns and indexes only; existing rows
are not touched (phone normalization of existing contacts is CRM-TASK-009).

- `contacts.whatsapp_bsuid` — WhatsApp business-scoped user id, the channel
  identity of visitors who only have a BSUID. Unique; NULLs don't collide.
- `contacts.wa_handover_*` — whether a human has taken over from the AI, until
  when, and the `occurred_at` of the event that last changed it. Naive UTC.
- `contacts.phone` index — lookup by normalized number.
- `messages.author_kind` / `bot_id` — who wrote the message (customer, AI bot,
  Director console, CRM agent). NULL for messages from before this.
- `messages.delivery_status` / `delivery_error` — Meta delivery status of
  messages the chatbot sent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("whatsapp_bsuid", sa.String(128), nullable=True))
    op.add_column(
        "contacts",
        sa.Column("wa_handover_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("contacts", sa.Column("wa_handover_expires_at", sa.DateTime(), nullable=True))
    op.add_column("contacts", sa.Column("wa_handover_updated_at", sa.DateTime(), nullable=True))
    op.create_index("idx_contacts_whatsapp_bsuid", "contacts", ["whatsapp_bsuid"], unique=True)
    op.create_index("idx_contacts_phone", "contacts", ["phone"])

    op.add_column(
        "messages",
        sa.Column(
            "author_kind",
            sa.Enum("customer", "ai", "console", "agent", name="message_author_kind"),
            nullable=True,
        ),
    )
    op.add_column("messages", sa.Column("bot_id", sa.String(50), nullable=True))
    op.add_column(
        "messages",
        sa.Column(
            "delivery_status",
            sa.Enum("sent", "delivered", "read", "failed", name="message_delivery_status"),
            nullable=True,
        ),
    )
    op.add_column("messages", sa.Column("delivery_error", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "delivery_error")
    op.drop_column("messages", "delivery_status")
    op.drop_column("messages", "bot_id")
    op.drop_column("messages", "author_kind")

    op.drop_index("idx_contacts_phone", table_name="contacts")
    op.drop_index("idx_contacts_whatsapp_bsuid", table_name="contacts")
    op.drop_column("contacts", "wa_handover_updated_at")
    op.drop_column("contacts", "wa_handover_expires_at")
    op.drop_column("contacts", "wa_handover_active")
    op.drop_column("contacts", "whatsapp_bsuid")
