"""make ai_requests.contact_id nullable

Revision ID: 016
Revises: 015
Create Date: 2026-10-19

AI features without a contact (the manager dashboard insight) record their
audit rows with an empty contact_id. The column type and the
idx_ai_requests_contact_id index stay as they are.

Downgrade deletes every ai_requests row whose contact_id is NULL before making
the column non-nullable again, so those audit rows are lost.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_requests") as batch_op:
        batch_op.alter_column("contact_id", existing_type=sa.String(36), nullable=True)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ai_requests WHERE contact_id IS NULL"))
    with op.batch_alter_table("ai_requests") as batch_op:
        batch_op.alter_column("contact_id", existing_type=sa.String(36), nullable=False)
