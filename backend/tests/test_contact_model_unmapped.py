"""The Contact model no longer maps the gateway flag (CRM-TASK-019).

docs/chatbot-replies.md section 8 and section 14, item 12: the column and its
index stay in the database (with a database default of false) until
CRM-TASK-020 drops them; the model stops mapping both so the running and the
rollback version are already free of it when that happens. The expected sets
below are spelled out in full, so a column or index coming back under the old
name fails them.
"""

from sqlalchemy import select

from app.models.contact import Contact

EXPECTED_COLUMNS = {
    "id",
    "name",
    "company",
    "industry",
    "email",
    "phone",
    "address",
    "notes",
    "assigned_to",
    "last_contact",
    "tags",
    "is_archived",
    "autocount_customer_code",
    "whatsapp_bsuid",
    "wa_handover_active",
    "wa_handover_expires_at",
    "wa_handover_updated_at",
    "reply_claimed_by",
    "reply_claimed_until",
    "deleted_at",
    "created_at",
    "updated_at",
}

EXPECTED_INDEXES = {
    "idx_industry",
    "idx_assigned_to",
    "idx_deleted_at",
    "idx_is_archived",
    "idx_autocount_customer_code",
    "idx_contacts_whatsapp_bsuid",
    "idx_contacts_phone",
}


def test_model_columns_leave_out_the_gateway_flag():
    """AC1 / AC4: the model's columns are exactly the ones still in use, without the gateway flag."""
    assert {c.name for c in Contact.__table__.columns} == EXPECTED_COLUMNS


def test_model_indexes_leave_out_the_gateway_index():
    """AC1: the model no longer declares the gateway flag's index."""
    assert {i.name for i in Contact.__table__.indexes} == EXPECTED_INDEXES


async def test_new_contact_is_created_on_tables_built_from_the_model(async_session_maker):
    """AC4: on tables created from the model a new contact is inserted and read back."""
    async with async_session_maker() as session:
        session.add(Contact(id="c-new", name="New visitor", phone="60123456789"))
        await session.commit()

    async with async_session_maker() as session:
        contact = (await session.execute(select(Contact).where(Contact.id == "c-new"))).scalar_one()

    assert contact.name == "New visitor"
    assert contact.phone == "60123456789"
    assert contact.wa_handover_active is False
