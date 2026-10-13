"""add the actual CRM sender to messages

Revision ID: 014
Revises: 013
Create Date: 2026-10-13

Store the actual CRM agent who sent an outbound reply separately from the
contact assignee. Existing messages retain a NULL value.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("sent_by", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "sent_by")
