"""Migration 012 and the model columns it adds (CRM-TASK-006).

The migration is run with Alembic's `Operations` against minimal `contacts`
(id, phone) and `messages` (id) tables in in-memory SQLite: just enough for
012 to work on, so the test does not depend on revisions 001-011.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models.contact import Contact
from app.models.message import Message

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "012_add_chatbot_event_fields.py"

CONTACT_COLUMNS = {
    "whatsapp_bsuid",
    "wa_handover_active",
    "wa_handover_expires_at",
    "wa_handover_updated_at",
}
MESSAGE_COLUMNS = {"author_kind", "bot_id", "delivery_status", "delivery_error"}
CONTACT_INDEXES = {"idx_contacts_whatsapp_bsuid", "idx_contacts_phone"}


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_012", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _minimal_tables(conn):
    metadata = sa.MetaData()
    sa.Table(
        "contacts",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("phone", sa.String(30), nullable=True),
    )
    sa.Table("messages", metadata, sa.Column("id", sa.String(36), primary_key=True))
    metadata.create_all(conn)


def _columns(conn, table):
    return {c["name"]: c for c in sa.inspect(conn).get_columns(table)}


def _indexes(conn, table):
    return {i["name"]: i for i in sa.inspect(conn).get_indexes(table)}


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _minimal_tables(connection)
        yield connection
    engine.dispose()


def test_revision_follows_011():
    """AC3: migration 012 with down_revision 011."""
    migration = _load_migration()
    assert migration.revision == "012"
    assert migration.down_revision == "011"


def test_upgrade_adds_columns_and_indexes_then_downgrade_removes_them(conn):
    """AC1, AC2, AC3: upgrade adds every listed column and index; downgrade
    returns both tables to their 011 shape."""
    migration = _load_migration()

    _run(conn, migration.upgrade)

    contacts = _columns(conn, "contacts")
    messages = _columns(conn, "messages")
    assert set(contacts) == {"id", "phone"} | CONTACT_COLUMNS
    assert set(messages) == {"id"} | MESSAGE_COLUMNS
    assert contacts["whatsapp_bsuid"]["nullable"] is True
    assert contacts["wa_handover_active"]["nullable"] is False
    assert contacts["wa_handover_expires_at"]["nullable"] is True
    assert contacts["wa_handover_updated_at"]["nullable"] is True
    assert all(messages[name]["nullable"] is True for name in MESSAGE_COLUMNS)

    indexes = _indexes(conn, "contacts")
    assert set(indexes) == CONTACT_INDEXES
    assert indexes["idx_contacts_whatsapp_bsuid"]["column_names"] == ["whatsapp_bsuid"]
    assert indexes["idx_contacts_whatsapp_bsuid"]["unique"]
    assert indexes["idx_contacts_phone"]["column_names"] == ["phone"]
    assert not indexes["idx_contacts_phone"]["unique"]
    assert _indexes(conn, "messages") == {}

    _run(conn, migration.downgrade)

    assert set(_columns(conn, "contacts")) == {"id", "phone"}
    assert set(_columns(conn, "messages")) == {"id"}
    assert _indexes(conn, "contacts") == {}
    assert _indexes(conn, "messages") == {}


def test_upgrade_leaves_existing_rows_alone_and_defaults_handover_to_false(conn):
    """AC1 (database default so existing rows read false) and AC3 (upgrade does
    not change existing data)."""
    conn.execute(sa.text("INSERT INTO contacts (id, phone) VALUES ('c1', '+60 12-345 6789')"))
    conn.execute(sa.text("INSERT INTO messages (id) VALUES ('m1')"))

    _run(conn, _load_migration().upgrade)

    contact = conn.execute(sa.text("SELECT * FROM contacts")).mappings().one()
    assert contact["phone"] == "+60 12-345 6789"
    assert contact["wa_handover_active"] == 0
    assert contact["whatsapp_bsuid"] is None
    assert contact["wa_handover_expires_at"] is None
    assert contact["wa_handover_updated_at"] is None

    message = conn.execute(sa.text("SELECT * FROM messages")).mappings().one()
    assert all(message[name] is None for name in MESSAGE_COLUMNS)


def test_model_matches_migration(conn):
    """AC3: model and migration agree column by column -- the columns 012
    creates reflect identically to the ones the models create."""
    _run(conn, _load_migration().upgrade)
    migrated_contacts = _columns(conn, "contacts")
    migrated_messages = _columns(conn, "messages")
    migrated_indexes = _indexes(conn, "contacts")

    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Contact.__table__, Message.__table__])
    with engine.connect() as model_conn:
        model_contacts = _columns(model_conn, "contacts")
        model_messages = _columns(model_conn, "messages")
        model_indexes = _indexes(model_conn, "contacts")
    engine.dispose()

    def shape(column):
        return (str(column["type"]), column["nullable"], column["default"])

    for name in CONTACT_COLUMNS:
        assert shape(migrated_contacts[name]) == shape(model_contacts[name]), name
    for name in MESSAGE_COLUMNS:
        assert shape(migrated_messages[name]) == shape(model_messages[name]), name
    for name in CONTACT_INDEXES:
        assert migrated_indexes[name]["column_names"] == model_indexes[name]["column_names"]
        assert migrated_indexes[name]["unique"] == model_indexes[name]["unique"]

    # SQLite stores the enums as VARCHAR, so check the allowed values on the model.
    columns = Message.__table__.c
    assert columns.author_kind.type.enums == ["customer", "ai", "console", "agent"]
    assert columns.delivery_status.type.enums == ["sent", "delivered", "read", "failed"]


@pytest.fixture
def session():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Contact.__table__, Message.__table__])
    with Session(engine) as s:
        yield s
    engine.dispose()


def test_two_contacts_cannot_share_a_bsuid(session):
    """AC1: whatsapp_bsuid has a unique index."""
    session.add(Contact(name="a", whatsapp_bsuid="MY.abc"))
    session.flush()
    session.add(Contact(name="b", whatsapp_bsuid="MY.abc"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_contacts_without_bsuid_do_not_collide(session):
    """AC1: multiple NULL whatsapp_bsuid values do not conflict; a new contact
    is not taken over by default."""
    session.add_all([Contact(name="a"), Contact(name="b")])
    session.flush()

    contacts = session.query(Contact).all()
    assert len(contacts) == 2
    assert all(c.whatsapp_bsuid is None for c in contacts)
    assert all(c.wa_handover_active is False for c in contacts)
