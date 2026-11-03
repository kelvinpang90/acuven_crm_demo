"""add template name, language and params columns to messages

Revision ID: 020
Revises: 019
Create Date: 2026-11-03

WhatsApp template messages sent by an agent (docs/chatbot-phase3.md, section 15
items 2 and 3): which template, language and parameters were used, so a retry
resends them unchanged. template_params is a JSON list of strings. No foreign
key and no index. Columns only; existing rows are not touched and get NULL.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("template_name", sa.String(100), nullable=True))
    op.add_column("messages", sa.Column("template_language", sa.String(20), nullable=True))
    op.add_column("messages", sa.Column("template_params", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "template_params")
    op.drop_column("messages", "template_language")
    op.drop_column("messages", "template_name")
