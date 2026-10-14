"""CRM-TASK-021 migration and model coverage for ``ai_requests``."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from app.database import Base
from app.models.ai_request import AiRequest

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "015_add_ai_requests.py"
_INDEXES = {"idx_ai_requests_contact_id", "idx_ai_requests_created_at"}


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_015", _MIGRATION)
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


def test_upgrade_creates_audit_table_and_indexes_then_downgrade_removes_them():
    """AC1 and AC2: 015 creates only ai_requests and its two ordinary indexes, then removes them."""
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            migration = _load_migration()
            assert migration.revision == "015"
            assert migration.down_revision == "014"

            _run(conn, migration.upgrade)

            columns = _columns(conn)
            assert set(columns) == {
                "id",
                "user_id",
                "contact_id",
                "feature",
                "request_id",
                "outcome",
                "created_at",
            }
            assert str(columns["id"]["type"]) == "VARCHAR(36)"
            assert str(columns["user_id"]["type"]) == "VARCHAR(36)"
            assert str(columns["contact_id"]["type"]) == "VARCHAR(36)"
            assert str(columns["feature"]["type"]) == "VARCHAR(64)"
            assert str(columns["request_id"]["type"]) == "VARCHAR(32)"
            assert all(
                columns[name]["nullable"] is False
                for name in {"id", "user_id", "contact_id", "feature", "outcome", "created_at"}
            )
            assert columns["request_id"]["nullable"] is True
            assert sa.inspect(conn).get_foreign_keys("ai_requests") == []

            indexes = _indexes(conn)
            assert set(indexes) == _INDEXES
            assert indexes["idx_ai_requests_contact_id"]["column_names"] == ["contact_id"]
            assert indexes["idx_ai_requests_created_at"]["column_names"] == ["created_at"]
            assert all(index["unique"] is False for index in indexes.values())

            _run(conn, migration.downgrade)

            assert "ai_requests" not in sa.inspect(conn).get_table_names()
    finally:
        engine.dispose()


def test_model_inserts_audit_record_and_restricts_outcome_values():
    """AC1 and AC3: the model stores the audit fields and accepts only the three outcome values."""
    engine = sa.create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine, tables=[AiRequest.__table__])
        columns = AiRequest.__table__.c
        assert columns.outcome.type.enums == ["ok", "unavailable", "error"]
        assert columns.created_at.type.timezone is False

        with Session(engine) as session:
            request = AiRequest(
                id="request-1",
                user_id="user-1",
                contact_id="contact-1",
                feature="crm.reply_draft",
                request_id="01HREQUESTID0000000000000000",
                outcome="ok",
            )
            session.add(request)
            session.flush()

            assert session.execute(sa.select(AiRequest)).scalar_one().outcome == "ok"

            session.add(
                AiRequest(
                    id="request-2",
                    user_id="user-1",
                    contact_id="contact-1",
                    feature="crm.reply_draft",
                    outcome="other",
                )
            )
            with pytest.raises(StatementError):
                session.flush()
    finally:
        engine.dispose()
