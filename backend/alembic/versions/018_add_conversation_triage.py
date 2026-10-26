"""add conversation triage labels and make ai_requests.user_id nullable

Revision ID: 018
Revises: 017
Create Date: 2026-10-26

Store the AI intent and urgency labels of each customer conversation, one row
per contact, without foreign keys. Audit rows of AI calls triggered by the
system (not by a staff member) have an empty user_id.

Downgrade deletes every ai_requests row whose user_id is NULL before making
the column non-nullable again, and drops conversation_triage, so those audit
rows and all labels are lost.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_triage",
        sa.Column("contact_id", sa.String(36), primary_key=True),
        sa.Column(
            "intent",
            sa.Enum(
                "inquiry",
                "quotation",
                "order",
                "complaint",
                "after_sales",
                "appointment",
                "other",
                name="conversation_triage_intent",
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "urgency",
            sa.Enum(
                "low",
                "normal",
                "high",
                "urgent",
                name="conversation_triage_urgency",
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(36), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=True),
        sa.Column("triaged_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("ai_requests") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ai_requests WHERE user_id IS NULL"))
    with op.batch_alter_table("ai_requests") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)
    op.drop_table("conversation_triage")
    sa.Enum(name="conversation_triage_intent").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="conversation_triage_urgency").drop(op.get_bind(), checkfirst=True)
