"""CRM-TASK-012 migration and model coverage for ``messages.sent_by``."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from app.database import Base
from app.models.message import Message

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "014_add_message_sent_by.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_014", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _columns(conn):
    return {column["name"]: column for column in sa.inspect(conn).get_columns("messages")}


def test_upgrade_adds_nullable_sent_by_and_downgrade_removes_it():
    """AC1 and AC2: 014 adds only nullable sent_by and downgrade restores 013."""
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            metadata = sa.MetaData()
            messages = sa.Table(
                "messages", metadata, sa.Column("id", sa.String(36), primary_key=True)
            )
            metadata.create_all(conn)
            conn.execute(sa.insert(messages).values(id="existing-message"))

            migration = _load_migration()
            assert migration.revision == "014"
            assert migration.down_revision == "013"

            _run(conn, migration.upgrade)

            columns = _columns(conn)
            assert set(columns) == {"id", "sent_by"}
            assert str(columns["sent_by"]["type"]) == "VARCHAR(36)"
            assert columns["sent_by"]["nullable"] is True
            assert sa.inspect(conn).get_foreign_keys("messages") == []
            assert sa.inspect(conn).get_indexes("messages") == []
            assert conn.execute(sa.text("SELECT sent_by FROM messages")).scalar_one() is None

            _run(conn, migration.downgrade)

            assert set(_columns(conn)) == {"id"}
    finally:
        engine.dispose()


def test_model_allows_message_without_sent_by():
    """AC2: the model maps nullable sent_by without requiring it on new messages."""
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[Message.__table__])
        sent_by = Message.__table__.c.sent_by
        assert isinstance(sent_by.type, sa.String)
        assert sent_by.type.length == 36
        assert sent_by.nullable is True
        with Session(engine) as session:
            message = Message(
                id="message-without-sender",
                channel="whatsapp",
                direction="outbound",
                sender_id="crm",
                recipient_id="visitor",
                body="Reply",
            )
            session.add(message)
            session.flush()

            assert message.sent_by is None
            assert session.execute(sa.select(Message.sent_by)).scalar_one() is None
    finally:
        engine.dispose()
