"""Opening a customer by id needs the same ownership the contact list applies.

Every by-id contact route checked only that the customer existed, so any rep
could read, edit or annotate anyone's customer -- and, through PUT, hand that
customer to themselves, after which every other check would wave them through.
A refusal answers exactly like a missing customer (404 NOT_FOUND).
"""

import pytest
from sqlalchemy import func, select

from app.models.contact import Contact
from app.models.deal import Deal
from app.services import contact_service
from tests._people import ADMIN, BOSS, OTHER, REP, logged_in_as, seed_people


async def _seed(session_maker) -> None:
    await seed_people(session_maker)
    async with session_maker() as session:
        session.add_all(
            [
                Contact(
                    id="c-demo",
                    name="Demo visitor",
                    phone="60144444444",
                    assigned_to="u-rep",
                ),
                Deal(id="d-mine", contact_id="c-mine", assigned_to="u-rep"),
                Deal(id="d-theirs", contact_id="c-theirs", assigned_to="u-other"),
            ]
        )
        await session.commit()


async def _owner(session_maker, contact_id: str):
    async with session_maker() as session:
        return (
            await session.execute(select(Contact.assigned_to).where(Contact.id == contact_id))
        ).scalar_one()


REP_CUSTOMER_ROUTES = [
    ("get", "/api/contacts/c-mine", None),
    ("put", "/api/contacts/c-mine", {"name": "Renamed"}),
    ("get", "/api/contacts/c-mine/autocount-documents", None),
    ("get", "/api/contacts/c-mine/activities", None),
    ("post", "/api/contacts/c-mine/activities", {"deal_id": "d-mine", "type": "phone"}),
]


@pytest.mark.parametrize("method, url, body", REP_CUSTOMER_ROUTES)
async def test_stranger_gets_not_found(client, async_session_maker, method, url, body):
    await _seed(async_session_maker)
    kwargs = {"json": body} if body is not None else {}
    with logged_in_as(OTHER):
        resp = await getattr(client, method)(url, **kwargs)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("method, url, body", REP_CUSTOMER_ROUTES)
@pytest.mark.parametrize("user", [REP, BOSS, ADMIN])
async def test_owner_team_and_admin_get_through(
    client, async_session_maker, user, method, url, body
):
    await _seed(async_session_maker)
    kwargs = {"json": body} if body is not None else {}
    with logged_in_as(user):
        resp = await getattr(client, method)(url, **kwargs)
    assert resp.status_code in (200, 201)


async def test_missing_and_foreign_customer_look_the_same(client, async_session_maker):
    await _seed(async_session_maker)
    with logged_in_as(OTHER):
        missing = await client.get("/api/contacts/c-nope")
        foreign = await client.get("/api/contacts/c-mine")
    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == foreign.json()


async def test_rep_cannot_take_someone_elses_customer(client, async_session_maker):
    await _seed(async_session_maker)
    with logged_in_as(REP):
        resp = await client.put("/api/contacts/c-theirs", json={"assigned_to": "u-rep"})
    assert resp.status_code == 404
    assert await _owner(async_session_maker, "c-theirs") == "u-other"


async def test_rep_cannot_hand_own_customer_outside_their_reach(client, async_session_maker):
    await _seed(async_session_maker)
    with logged_in_as(REP):
        resp = await client.put("/api/contacts/c-mine", json={"assigned_to": "u-other"})
    assert resp.status_code == 403


async def test_rep_may_resubmit_their_own_assignment(client, async_session_maker):
    """The edit form may send the unchanged owner back; that is not a reassignment."""
    await _seed(async_session_maker)
    with logged_in_as(REP):
        resp = await client.put(
            "/api/contacts/c-mine", json={"name": "Renamed", "assigned_to": "u-rep"}
        )
    assert resp.status_code == 200


async def test_manager_reassigns_within_team_only(client, async_session_maker):
    await _seed(async_session_maker)
    with logged_in_as(BOSS):
        inside = await client.put("/api/contacts/c-mine", json={"assigned_to": "u-boss"})
        outside = await client.put("/api/contacts/c-mine", json={"assigned_to": "u-other"})
    assert inside.status_code == 200
    assert outside.status_code == 403


async def test_manager_archives_within_team_only(client, async_session_maker):
    await _seed(async_session_maker)
    with logged_in_as(BOSS):
        theirs = await client.patch("/api/contacts/c-theirs/archive", json={"is_archived": 1})
        mine = await client.patch("/api/contacts/c-mine/archive", json={"is_archived": 1})
    assert theirs.status_code == 404
    assert mine.status_code == 200


async def test_activity_cannot_borrow_another_customers_deal(client, async_session_maker):
    await _seed(async_session_maker)
    with logged_in_as(ADMIN):
        resp = await client.post(
            "/api/contacts/c-mine/activities", json={"deal_id": "d-theirs", "type": "phone"}
        )
    assert resp.status_code == 404


async def test_demo_visitor_opens_for_its_rep(client, async_session_maker):
    """Task one keeps demo contacts reachable by id; ownership must not undo that."""
    await _seed(async_session_maker)
    with logged_in_as(REP):
        resp = await client.get("/api/contacts/c-demo")
    assert resp.status_code == 200


async def test_import_cannot_add_deal_to_someone_elses_customer(async_session_maker):
    await _seed(async_session_maker)
    rows = [{"id": "c-theirs", "status": "lead", "deal_value": "100"}]
    async with async_session_maker() as session:
        result = await contact_service.import_contacts(session, rows, REP)
        await session.commit()
    assert result["errors"] and result["errors"][0]["field"] == "customer_id"
    async with async_session_maker() as session:
        deals = (
            await session.execute(
                select(func.count()).select_from(Deal).where(Deal.contact_id == "c-theirs")
            )
        ).scalar()
    assert deals == 1
