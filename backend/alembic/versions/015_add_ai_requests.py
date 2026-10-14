"""add AI request audit records

Revision ID: 015
Revises: 014
Create Date: 2026-10-14

Store metadata for each inbox AI request without storing request or response
content.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("contact_id", sa.String(36), nullable=False),
        sa.Column("feature", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(
                "ok",
                "unavailable",
                "error",
                name="ai_request_outcome",
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_ai_requests_contact_id", "ai_requests", ["contact_id"])
    op.create_index("idx_ai_requests_created_at", "ai_requests", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_requests")
