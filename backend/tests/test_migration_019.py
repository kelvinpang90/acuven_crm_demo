"""CRM-TASK-091 migration and model coverage for ``contacts.reply_claimed_by`` and
``contacts.reply_claimed_until``."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from app.database import Base
from app.models.contact import Contact

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_CLAIM_COLUMNS = {"reply_claimed_by", "reply_claimed_until"}


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_019", _VERSIONS / "019_add_contact_reply_claim.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _columns(conn, table):
    return {column["name"]: column for column in sa.inspect(conn).get_columns(table)}


def test_migration_revises_018():
    """AC2: migration 019 has down_revision 018."""
    migration = _load_migration()
    assert migration.revision == "019"
    assert migration.down_revision == "018"


def test_upgrade_adds_claim_columns_then_downgrade_removes_them():
    """AC1 and AC2: on a minimal contacts table with only id, upgrade adds both columns
    (String(36) and DateTime, nullable, no foreign key, no index) and leaves existing rows
    NULL in both; downgrade removes them, back to the 018 shape with the rows kept."""
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            metadata = sa.MetaData()
            sa.Table("contacts", metadata, sa.Column("id", sa.String(36), primary_key=True))
            metadata.create_all(conn)
            conn.execute(sa.text("INSERT INTO contacts (id) VALUES ('contact-1'), ('contact-2')"))

            _run(conn, migration.upgrade)

            columns = _columns(conn, "contacts")
            assert set(columns) == {"id"} | _CLAIM_COLUMNS
            assert str(columns["reply_claimed_by"]["type"]) == "VARCHAR(36)"
            assert isinstance(columns["reply_claimed_until"]["type"], sa.DateTime)
            assert columns["reply_claimed_until"]["type"].timezone is False
            assert columns["reply_claimed_by"]["nullable"] is True
            assert columns["reply_claimed_until"]["nullable"] is True
            inspector = sa.inspect(conn)
            assert inspector.get_foreign_keys("contacts") == []
            assert inspector.get_indexes("contacts") == []

            rows = conn.execute(
                sa.text(
                    "SELECT id, reply_claimed_by, reply_claimed_until FROM contacts ORDER BY id"
                )
            ).all()
            assert [tuple(row) for row in rows] == [
                ("contact-1", None, None),
                ("contact-2", None, None),
            ]

            _run(conn, migration.downgrade)

            assert set(_columns(conn, "contacts")) == {"id"}
            rows = conn.execute(sa.text("SELECT id FROM contacts ORDER BY id")).all()
            assert [tuple(row) for row in rows] == [("contact-1",), ("contact-2",)]
    finally:
        engine.dispose()


def test_model_matches_migration_columns():
    """AC2: the model declares the two columns exactly as the migration adds them, without
    foreign keys or indexes."""
    columns = Contact.__table__.c
    assert isinstance(columns.reply_claimed_by.type, sa.String)
    assert columns.reply_claimed_by.type.length == 36
    assert isinstance(columns.reply_claimed_until.type, sa.DateTime)
    assert columns.reply_claimed_until.type.timezone is False
    assert columns.reply_claimed_by.nullable is True
    assert columns.reply_claimed_until.nullable is True
    assert not columns.reply_claimed_by.foreign_keys
    assert not columns.reply_claimed_until.foreign_keys
    indexed = {column.name for index in Contact.__table__.indexes for column in index.columns}
    assert not indexed & _CLAIM_COLUMNS


def test_contact_created_without_claim_columns_has_them_empty():
    """AC1: a table built from the Contact model accepts a contact inserted without the two
    columns, and both are NULL."""
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[Contact.__table__])
        with Session(engine) as session:
            session.add(Contact(name="a"))
            session.flush()

            stored = session.execute(sa.select(Contact)).scalar_one()
            assert stored.reply_claimed_by is None
            assert stored.reply_claimed_until is None

            raw = session.execute(
                sa.text("SELECT reply_claimed_by, reply_claimed_until FROM contacts")
            ).one()
            assert tuple(raw) == (None, None)
    finally:
        engine.dispose()
