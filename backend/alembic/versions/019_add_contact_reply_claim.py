"""add reply claim columns to contacts

Revision ID: 019
Revises: 018
Create Date: 2026-10-31

Conversation claim (docs/chatbot-phase3.md, sections 9 and 14 item 2):
who is replying to the contact right now and until when. Naive UTC, same as
wa_handover_expires_at. No foreign key (like assigned_to) and no index.
Columns only; existing rows are not touched and get NULL in both.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("reply_claimed_by", sa.String(36), nullable=True))
    op.add_column("contacts", sa.Column("reply_claimed_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "reply_claimed_until")
    op.drop_column("contacts", "reply_claimed_by")
