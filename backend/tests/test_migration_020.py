"""CRM-TASK-096 migration and model coverage for ``messages.template_name``,
``messages.template_language`` and ``messages.template_params``."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from app.database import Base
from app.models.message import Message

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_TEMPLATE_COLUMNS = {"template_name", "template_language", "template_params"}


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_020", _VERSIONS / "020_add_message_template_fields.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _columns(conn, table):
    return {column["name"]: column for column in sa.inspect(conn).get_columns(table)}


def _message(**fields):
    return Message(
        channel="whatsapp",
        direction="outbound",
        sender_id="crm",
        recipient_id="visitor",
        body="Hello",
        **fields,
    )


def test_migration_revises_019():
    """AC2: migration 020 has down_revision 019."""
    migration = _load_migration()
    assert migration.revision == "020"
    assert migration.down_revision == "019"


def test_upgrade_adds_template_columns_then_downgrade_removes_them():
    """AC1 and AC2: on a minimal messages table with only id, upgrade adds the three columns
    (String(100), String(20) and JSON, nullable, no foreign key, no index) and leaves existing
    rows NULL in all three; downgrade removes them, back to the 019 shape with the rows kept."""
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            metadata = sa.MetaData()
            sa.Table("messages", metadata, sa.Column("id", sa.String(36), primary_key=True))
            metadata.create_all(conn)
            conn.execute(sa.text("INSERT INTO messages (id) VALUES ('message-1'), ('message-2')"))

            _run(conn, migration.upgrade)

            columns = _columns(conn, "messages")
            assert set(columns) == {"id"} | _TEMPLATE_COLUMNS
            assert str(columns["template_name"]["type"]) == "VARCHAR(100)"
            assert str(columns["template_language"]["type"]) == "VARCHAR(20)"
            assert isinstance(columns["template_params"]["type"], sa.JSON)
            for name in _TEMPLATE_COLUMNS:
                assert columns[name]["nullable"] is True
            inspector = sa.inspect(conn)
            assert inspector.get_foreign_keys("messages") == []
            assert inspector.get_indexes("messages") == []

            rows = conn.execute(
                sa.text(
                    "SELECT id, template_name, template_language, template_params"
                    " FROM messages ORDER BY id"
                )
            ).all()
            assert [tuple(row) for row in rows] == [
                ("message-1", None, None, None),
                ("message-2", None, None, None),
            ]

            _run(conn, migration.downgrade)

            assert set(_columns(conn, "messages")) == {"id"}
            rows = conn.execute(sa.text("SELECT id FROM messages ORDER BY id")).all()
            assert [tuple(row) for row in rows] == [("message-1",), ("message-2",)]
    finally:
        engine.dispose()


def test_model_matches_migration_columns():
    """AC2: the model declares the three columns exactly as the migration adds them, without
    foreign keys or indexes."""
    columns = Message.__table__.c
    assert isinstance(columns.template_name.type, sa.String)
    assert columns.template_name.type.length == 100
    assert isinstance(columns.template_language.type, sa.String)
    assert columns.template_language.type.length == 20
    assert isinstance(columns.template_params.type, sa.JSON)
    for name in _TEMPLATE_COLUMNS:
        assert columns[name].nullable is True
        assert not columns[name].foreign_keys
    indexed = {column.name for index in Message.__table__.indexes for column in index.columns}
    assert not indexed & _TEMPLATE_COLUMNS


def test_message_created_without_template_columns_has_them_empty():
    """AC1: a table built from the Message model accepts a message inserted without the three
    columns, and all three are NULL."""
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[Message.__table__])
        with Session(engine) as session:
            session.add(_message())
            session.flush()

            stored = session.execute(sa.select(Message)).scalar_one()
            assert stored.template_name is None
            assert stored.template_language is None
            assert stored.template_params is None

            raw = session.execute(
                sa.text("SELECT template_name, template_language, template_params FROM messages")
            ).one()
            assert tuple(raw) == (None, None, None)
    finally:
        engine.dispose()


def test_message_with_template_params_reads_back_same_list():
    """AC1: template_params stores a list of strings and reads back the same list."""
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[Message.__table__])
        params = ["Ali", "10:30", "Ali"]
        with Session(engine) as session:
            session.add(
                _message(
                    id="template-message",
                    template_name="appointment_reminder",
                    template_language="en",
                    template_params=params,
                )
            )
            session.commit()

        with Session(engine) as session:
            stored = session.get(Message, "template-message")
            assert stored.template_name == "appointment_reminder"
            assert stored.template_language == "en"
            assert stored.template_params == params
    finally:
        engine.dispose()
