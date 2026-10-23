"""Deals record the ai_chatbot_demo industry bot they came in through.

`POST /api/contacts` takes `initial_source` for the deal it creates alongside the
contact, `POST /api/deals` takes `source`; the pipeline card shows the deal's
source and the contact list shows the distinct sources of a contact's deals.
"""

import importlib.util
from pathlib import Path

import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.database import get_db
from app.main import app
from tests._people import ADMIN, logged_in_as, seed_people

_MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "017_add_deal_source.py"


@pytest_asyncio.fixture
async def committing_client(client, async_session_maker):
    """`client`, but each request commits like the real `get_db`: creating a
    contact does not commit on its own."""

    async def _get_db_committing():
        async with async_session_maker() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = _get_db_committing
    yield client


async def _create_contact(client, **extra):
    with logged_in_as(ADMIN):
        resp = await client.post("/api/contacts", json={"name": "Visitor", **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


async def _create_deal(client, contact_id, **extra):
    with logged_in_as(ADMIN):
        return await client.post("/api/deals", json={"contact_id": contact_id, **extra})


async def _pipeline_cards(client):
    with logged_in_as(ADMIN):
        resp = await client.get("/api/pipeline")
    return [deal for stage in resp.json()["data"]["stages"] for deal in stage["deals"]]


async def _listed_contact(client, contact_id):
    with logged_in_as(ADMIN):
        resp = await client.get("/api/contacts")
    return next(c for c in resp.json()["data"]["data"] if c["id"] == contact_id)


async def test_contact_create_puts_initial_source_on_its_deal(
    committing_client, async_session_maker
):
    await seed_people(async_session_maker)
    contact_id = await _create_contact(committing_client, initial_source="realestate")

    cards = [d for d in await _pipeline_cards(committing_client) if d["contact_id"] == contact_id]
    assert [d["source"] for d in cards] == ["realestate"]
    assert (await _listed_contact(committing_client, contact_id))["sources"] == ["realestate"]


async def test_deal_create_takes_source(committing_client, async_session_maker):
    await seed_people(async_session_maker)
    contact_id = await _create_contact(committing_client)

    resp = await _create_deal(committing_client, contact_id, source="retail")
    assert resp.status_code == 201
    assert resp.json()["data"]["source"] == "retail"


async def test_contact_lists_distinct_sorted_sources_of_its_deals(
    committing_client, async_session_maker
):
    await seed_people(async_session_maker)
    contact_id = await _create_contact(committing_client, initial_source="saas")
    for source in ("retail", "saas", None):
        resp = await _create_deal(
            committing_client, contact_id, **({"source": source} if source else {})
        )
        assert resp.status_code == 201

    listed = await _listed_contact(committing_client, contact_id)
    assert listed["deal_count"] == 4
    assert listed["sources"] == ["retail", "saas"]
    with logged_in_as(ADMIN):
        detail = await committing_client.get(f"/api/contacts/{contact_id}")
    assert detail.json()["data"]["sources"] == ["retail", "saas"]


async def test_no_source_reads_as_none(committing_client, async_session_maker):
    await seed_people(async_session_maker)
    contact_id = await _create_contact(committing_client)

    cards = [d for d in await _pipeline_cards(committing_client) if d["contact_id"] == contact_id]
    assert [d["source"] for d in cards] == [None]
    assert (await _listed_contact(committing_client, contact_id))["sources"] == []


async def test_unknown_source_is_rejected(committing_client, async_session_maker):
    await seed_people(async_session_maker)
    with logged_in_as(ADMIN):
        resp = await committing_client.post(
            "/api/contacts", json={"name": "X", "initial_source": "casino"}
        )
    assert resp.status_code == 422
    contact_id = await _create_contact(committing_client)
    assert (await _create_deal(committing_client, contact_id, source="casino")).status_code == 422


def test_migration_017_adds_nullable_source_and_downgrade_removes_it():
    spec = importlib.util.spec_from_file_location("migration_017", _MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("017", "016")

    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            metadata = sa.MetaData()
            deals = sa.Table("deals", metadata, sa.Column("id", sa.String(36), primary_key=True))
            metadata.create_all(conn)
            conn.execute(sa.insert(deals).values(id="existing-deal"))

            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            columns = {c["name"]: c for c in sa.inspect(conn).get_columns("deals")}
            assert str(columns["source"]["type"]) == "VARCHAR(20)"
            assert columns["source"]["nullable"] is True
            assert conn.execute(sa.text("SELECT source FROM deals")).scalar_one() is None

            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert {c["name"] for c in sa.inspect(conn).get_columns("deals")} == {"id"}
    finally:
        engine.dispose()
