"""CRM-TASK-065 migration and model coverage for ``conversation_triage`` and nullable
``ai_requests.user_id``."""

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ConversationTriage

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_TRIAGE_COLUMNS = {"contact_id", "intent", "urgency", "message_id", "request_id", "triaged_at"}


def _load_migration(filename, name):
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _columns(conn, table):
    return {column["name"]: column for column in sa.inspect(conn).get_columns(table)}


def _insert_audit(conn, row_id, user_id):
    conn.execute(
        sa.text(
            "INSERT INTO ai_requests (id, user_id, contact_id, feature, outcome, created_at) "
            "VALUES (:id, :user_id, 'contact-1', 'crm.message_triage', 'ok', "
            "'2026-10-26 00:00:00')"
        ),
        {"id": row_id, "user_id": user_id},
    )


def _upgraded(conn):
    migration_015 = _load_migration("015_add_ai_requests.py", "migration_015_for_018")
    migration_016 = _load_migration("016_ai_requests_contact_nullable.py", "migration_016_for_018")
    migration_018 = _load_migration("018_add_conversation_triage.py", "migration_018")
    assert migration_018.revision == "018"
    assert migration_018.down_revision == "017"
    _run(conn, migration_015.upgrade)
    _run(conn, migration_016.upgrade)
    _run(conn, migration_018.upgrade)
    return migration_018


def test_upgrade_creates_triage_table_and_makes_user_id_nullable():
    """AC1 (upgrade) and test case 1: after 015, 016 then 018, conversation_triage exists with
    the specified columns, primary key and no foreign keys, ai_requests.user_id is nullable,
    and an audit row without a user can be inserted."""
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            _upgraded(conn)

            inspector = sa.inspect(conn)
            assert "conversation_triage" in inspector.get_table_names()
            columns = _columns(conn, "conversation_triage")
            assert set(columns) == _TRIAGE_COLUMNS
            assert str(columns["contact_id"]["type"]) == "VARCHAR(36)"
            assert str(columns["message_id"]["type"]) == "VARCHAR(36)"
            assert str(columns["request_id"]["type"]) == "VARCHAR(32)"
            assert all(
                columns[name]["nullable"] is False
                for name in {"contact_id", "intent", "urgency", "message_id", "triaged_at"}
            )
            assert columns["request_id"]["nullable"] is True
            assert inspector.get_pk_constraint("conversation_triage")["constrained_columns"] == [
                "contact_id"
            ]
            assert inspector.get_foreign_keys("conversation_triage") == []

            audit_columns = _columns(conn, "ai_requests")
            assert audit_columns["user_id"]["nullable"] is True
            assert str(audit_columns["user_id"]["type"]) == "VARCHAR(36)"

            _insert_audit(conn, "request-1", None)
            assert (
                conn.execute(
                    sa.text("SELECT user_id FROM ai_requests WHERE id = 'request-1'")
                ).scalar_one()
                is None
            )
    finally:
        engine.dispose()


def test_downgrade_drops_triage_table_and_restores_not_null_user_id():
    """AC2 (downgrade) and test case 2: 018 downgrade drops conversation_triage, deletes audit
    rows with a NULL user_id, keeps the others and makes user_id non-nullable again."""
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            migration_018 = _upgraded(conn)
            _insert_audit(conn, "request-null", None)
            _insert_audit(conn, "request-user", "user-1")

            _run(conn, migration_018.downgrade)

            assert "conversation_triage" not in sa.inspect(conn).get_table_names()

            rows = conn.execute(sa.text("SELECT id, user_id FROM ai_requests")).all()
            assert [tuple(row) for row in rows] == [("request-user", "user-1")]

            audit_columns = _columns(conn, "ai_requests")
            assert audit_columns["user_id"]["nullable"] is False
            assert str(audit_columns["user_id"]["type"]) == "VARCHAR(36)"

            with pytest.raises(IntegrityError):
                _insert_audit(conn, "request-null-again", None)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("intent", "urgency"),
    [("unknown", "normal"), ("inquiry", "critical")],
)
def test_model_accepts_only_enum_values_for_intent_and_urgency(intent, urgency):
    """AC3 and test case 3: a table built from the ConversationTriage model stores enum values
    and rejects an intent or urgency outside the enums."""
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[ConversationTriage.__table__])
        columns = ConversationTriage.__table__.c
        assert columns.intent.type.enums == [
            "inquiry",
            "quotation",
            "order",
            "complaint",
            "after_sales",
            "appointment",
            "other",
        ]
        assert columns.urgency.type.enums == ["low", "normal", "high", "urgent"]
        assert columns.triaged_at.type.timezone is False

        with Session(engine) as session:
            session.add(
                ConversationTriage(
                    contact_id="contact-1",
                    intent="complaint",
                    urgency="urgent",
                    message_id="message-1",
                    request_id=None,
                    triaged_at=datetime(2026, 10, 26, 0, 0, 0),
                )
            )
            session.flush()

            stored = session.execute(sa.select(ConversationTriage)).scalar_one()
            assert (stored.intent, stored.urgency) == ("complaint", "urgent")

            session.add(
                ConversationTriage(
                    contact_id="contact-2",
                    intent=intent,
                    urgency=urgency,
                    message_id="message-2",
                    triaged_at=datetime(2026, 10, 26, 0, 0, 0),
                )
            )
            with pytest.raises(StatementError):
                session.flush()
    finally:
        engine.dispose()
