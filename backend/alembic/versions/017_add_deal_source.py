"""add the enquiry source to deals

Revision ID: 017
Revises: 016
Create Date: 2026-10-23

Record which ai_chatbot_demo industry bot (banking, food, hotel, realestate,
retail, saas) a deal came in through. Existing deals keep a NULL value.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("source", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("deals", "source")
