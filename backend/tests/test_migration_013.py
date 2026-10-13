"""CRM-TASK-009 stored-phone normalization migration tests."""

import importlib.util
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import Base
from app.models.contact import Contact

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIGRATION = _VERSIONS / "013_normalize_contact_phones.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_013", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        step()


def _rows(conn):
    return {
        row["id"]: dict(row)
        for row in conn.execute(
            sa.select(
                Contact.__table__.c.id,
                Contact.__table__.c.phone,
                Contact.__table__.c.updated_at,
                Contact.__table__.c.deleted_at,
            ).order_by(Contact.__table__.c.id)
        ).mappings()
    }


def test_upgrade_normalizes_only_phone_and_is_idempotent():
    """AC1/AC2/AC3: all stored forms, including a soft-deleted contact, use
    the shared normalizer; invalid and empty values stay as-is, no contacts
    merge, no non-phone column or schema changes, and reruns stay no-ops."""
    migration = _load_migration()
    assert migration.revision == "013"
    assert migration.down_revision == "012"

    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            Base.metadata.create_all(conn, tables=[Contact.__table__])
            schema_before = {
                "columns": [column["name"] for column in sa.inspect(conn).get_columns("contacts")],
                "indexes": sorted(
                    index["name"] for index in sa.inspect(conn).get_indexes("contacts")
                ),
            }
            saved_at = datetime(2026, 10, 1, 12, 0, 0)
            deleted_at = datetime(2026, 10, 2, 12, 0, 0)
            conn.execute(
                sa.insert(Contact.__table__),
                [
                    {
                        "id": "formatted",
                        "name": "Formatted",
                        "phone": "+60 12-345 6789",
                        "deleted_at": None,
                        "created_at": saved_at,
                        "updated_at": saved_at,
                    },
                    {
                        "id": "local",
                        "name": "Local",
                        "phone": "012-345 6789",
                        "deleted_at": None,
                        "created_at": saved_at,
                        "updated_at": saved_at,
                    },
                    {
                        "id": "international",
                        "name": "International",
                        "phone": "0060123456789",
                        "deleted_at": None,
                        "created_at": saved_at,
                        "updated_at": saved_at,
                    },
                    {
                        "id": "normalized",
                        "name": "Normalized",
                        "phone": "60123456789",
                        "deleted_at": None,
                        "created_at": saved_at,
                        "updated_at": saved_at,
                    },
                    {
                        "id": "invalid",
                        "name": "Invalid",
                        "phone": "03-1234",
                        "deleted_at": None,
                        "created_at": saved_at,
                        "updated_at": saved_at,
                    },
                    {
                        "id": "empty",
                        "name": "Empty",
                        "phone": None,
                        "deleted_at": None,
                        "created_at": saved_at,
                        "updated_at": saved_at,
                    },
                    {
                        "id": "deleted",
                        "name": "Deleted",
                        "phone": "012-999 9999",
                        "deleted_at": deleted_at,
                        "created_at": saved_at,
                        "updated_at": saved_at,
                    },
                ],
            )

            before = _rows(conn)
            assert before["deleted"]["deleted_at"] == deleted_at
            _run(conn, migration.upgrade)
            once = _rows(conn)
            _run(conn, migration.upgrade)
            twice = _rows(conn)
            _run(conn, migration.downgrade)
            after_downgrade = _rows(conn)

            assert {contact_id: row["phone"] for contact_id, row in once.items()} == {
                "deleted": "60129999999",
                "empty": None,
                "formatted": "60123456789",
                "international": "60123456789",
                "invalid": "03-1234",
                "local": "60123456789",
                "normalized": "60123456789",
            }
            assert len(once) == 7
            assert once["deleted"]["deleted_at"] == deleted_at
            assert all(
                row["updated_at"] == before[contact_id]["updated_at"]
                for contact_id, row in once.items()
            )
            assert twice == once
            assert after_downgrade == once
            assert {
                "columns": [column["name"] for column in sa.inspect(conn).get_columns("contacts")],
                "indexes": sorted(
                    index["name"] for index in sa.inspect(conn).get_indexes("contacts")
                ),
            } == schema_before
    finally:
        engine.dispose()
