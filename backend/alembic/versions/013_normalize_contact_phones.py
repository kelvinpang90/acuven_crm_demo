"""normalize stored contact phones

Revision ID: 013
Revises: 012
Create Date: 2026-10-13

Rewrite only normalizable, non-empty ``contacts.phone`` values using the
shared CRM-TASK-007 normalizer.  This data migration deliberately includes
soft-deleted contacts.  It does not merge or delete contacts that normalize to
the same number, and it does not alter the table schema.

Downgrade is intentionally a no-op: the original phone spellings are not
retained, so the data cannot be restored.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.utils.phone import normalize_phone

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Normalize changed phone values without updating any other column."""
    connection = op.get_bind()
    contacts = sa.table("contacts", sa.column("id"), sa.column("phone"))

    for contact_id, phone in connection.execute(sa.select(contacts.c.id, contacts.c.phone)):
        normalized = normalize_phone(phone)
        if normalized is not None and normalized != phone:
            connection.execute(
                contacts.update().where(contacts.c.id == contact_id).values(phone=normalized)
            )


def downgrade() -> None:
    """Leave data untouched because the original phone spellings are unavailable."""
