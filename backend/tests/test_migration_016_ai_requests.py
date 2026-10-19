"""CRM-TASK-039 migration and model coverage for nullable ``ai_requests.contact_id``."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models.ai_request import AiRequest

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_INDEXES = {"idx_ai_requests_contact_id", "idx_ai_requests_created_at"}


def _load_migration(filename, name):
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _columns(conn):
    return {column["name"]: column for column in sa.inspect(conn).get_columns("ai_requests")}


def _indexes(conn):
    return {index["name"]: index for index in sa.inspect(conn).get_indexes("ai_requests")}


def _insert(conn, row_id, contact_id):
    conn.execute(
        sa.text(
            "INSERT INTO ai_requests (id, user_id, contact_id, feature, outcome, created_at) "
            "VALUES (:id, 'user-1', :contact_id, 'crm.dashboard_insight', 'ok', "
            "'2026-10-19 00:00:00')"
        ),
        {"id": row_id, "contact_id": contact_id},
    )


def _upgraded(conn):
    migration_015 = _load_migration("015_add_ai_requests.py", "migration_015_for_016")
    migration_016 = _load_migration("016_ai_requests_contact_nullable.py", "migration_016")
    assert migration_016.revision == "016"
    assert migration_016.down_revision == "015"
    _run(conn, migration_015.upgrade)
    _run(conn, migration_016.upgrade)
    return migration_016


def _assert_indexes(conn):
    indexes = _indexes(conn)
    assert set(indexes) == _INDEXES
    assert indexes["idx_ai_requests_contact_id"]["column_names"] == ["contact_id"]
    assert indexes["idx_ai_requests_created_at"]["column_names"] == ["created_at"]


def test_upgrade_makes_contact_id_nullable_and_keeps_indexes():
    """AC1 (upgrade) and test case 1: after 015 then 016, contact_id is a nullable
    VARCHAR(36), both indexes remain, and a row without a contact can be inserted."""
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            _upgraded(conn)

            columns = _columns(conn)
            assert columns["contact_id"]["nullable"] is True
            assert str(columns["contact_id"]["type"]) == "VARCHAR(36)"
            _assert_indexes(conn)

            _insert(conn, "request-1", None)
            assert (
                conn.execute(
                    sa.text("SELECT contact_id FROM ai_requests WHERE id = 'request-1'")
                ).scalar_one()
                is None
            )
    finally:
        engine.dispose()


def test_downgrade_deletes_rows_without_contact_and_restores_not_null():
    """AC1 (downgrade) and test case 2: 016 downgrade deletes rows with a NULL contact_id,
    keeps the others, makes contact_id non-nullable again and keeps both indexes."""
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            migration_016 = _upgraded(conn)
            _insert(conn, "request-null", None)
            _insert(conn, "request-contact", "contact-1")

            _run(conn, migration_016.downgrade)

            rows = conn.execute(sa.text("SELECT id, contact_id FROM ai_requests")).all()
            assert [tuple(row) for row in rows] == [("request-contact", "contact-1")]

            columns = _columns(conn)
            assert columns["contact_id"]["nullable"] is False
            assert str(columns["contact_id"]["type"]) == "VARCHAR(36)"
            _assert_indexes(conn)

            with pytest.raises(IntegrityError):
                _insert(conn, "request-null-again", None)
    finally:
        engine.dispose()


def test_model_inserts_audit_record_without_contact():
    """AC2 and test case 3: a table built from the AiRequest model accepts a NULL contact_id."""
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[AiRequest.__table__])
        assert AiRequest.__table__.c.contact_id.nullable is True

        with Session(engine) as session:
            session.add(
                AiRequest(
                    id="request-1",
                    user_id="user-1",
                    contact_id=None,
                    feature="crm.dashboard_insight",
                    outcome="ok",
                )
            )
            session.flush()

            assert session.execute(sa.select(AiRequest)).scalar_one().contact_id is None
    finally:
        engine.dispose()
