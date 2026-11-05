"""Reading a conversation needs the same ownership as answering it.

Sending was guarded by `_may_message_contact`, but the conversation itself
(`GET /api/messages/contact/{id}`) and the read flag
(`PATCH /api/messages/{id}/read`) answered anyone who was logged in. The demo
flow has to survive the fix: a demo visitor is routed to a rep like any other
customer, and that rep must still be able to read and answer them.
"""

import pytest
from sqlalchemy import select

from app.models.contact import Contact
from app.models.message import Message
from app.services import access_service
from tests._people import ADMIN, BOSS, OTHER, REP, logged_in_as, seed_people


@pytest.mark.parametrize(
    "user, owner_id, allowed",
    [
        (REP, "u-rep", True),
        (REP, "u-other", False),
        (REP, None, False),
        (BOSS, "u-rep", True),
        (BOSS, "u-boss", True),
        (BOSS, "u-other", False),
        (BOSS, None, False),
        (OTHER, "u-rep", False),
        (ADMIN, "u-other", True),
        (ADMIN, None, True),
    ],
)
async def test_owner_matrix(async_session_maker, user, owner_id, allowed):
    await seed_people(async_session_maker)
    async with async_session_maker() as session:
        assert await access_service.may_access_owner(session, user, owner_id) is allowed


async def _seed_conversations(session_maker) -> None:
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
                Message(
                    id="m-mine",
                    contact_id="c-mine",
                    channel="whatsapp",
                    direction="inbound",
                    sender_id="60111111111",
                    recipient_id="biz",
                    body="mine-secret",
                    assigned_to="u-rep",
                ),
                Message(
                    id="m-demo",
                    contact_id="c-demo",
                    channel="whatsapp",
                    direction="inbound",
                    sender_id="60144444444",
                    recipient_id="biz",
                    body="demo hi",
                    assigned_to="u-rep",
                ),
            ]
        )
        await session.commit()


async def _is_read(session_maker, message_id: str) -> bool:
    async with session_maker() as session:
        return (
            await session.execute(select(Message.is_read).where(Message.id == message_id))
        ).scalar_one()


async def test_stranger_cannot_read_conversation(client, async_session_maker):
    await _seed_conversations(async_session_maker)
    with logged_in_as(OTHER):
        resp = await client.get("/api/messages/contact/c-mine")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "NOT_ASSIGNED"
    assert "mine-secret" not in resp.text


async def test_stranger_cannot_mark_read(client, async_session_maker):
    await _seed_conversations(async_session_maker)
    with logged_in_as(OTHER):
        resp = await client.patch("/api/messages/m-mine/read")
    assert resp.status_code == 403
    assert await _is_read(async_session_maker, "m-mine") is False


async def test_missing_message_refused_like_foreign_one(client, async_session_maker):
    """A rep cannot tell a message id that does not exist from one that is not theirs."""
    await _seed_conversations(async_session_maker)
    with logged_in_as(OTHER):
        missing = await client.patch("/api/messages/m-nope/read")
        foreign = await client.patch("/api/messages/m-mine/read")
    assert (missing.status_code, missing.json()) == (foreign.status_code, foreign.json())


@pytest.mark.parametrize("user", [REP, BOSS, ADMIN])
async def test_owner_team_and_admin_read_conversation(client, async_session_maker, user):
    await _seed_conversations(async_session_maker)
    with logged_in_as(user):
        resp = await client.get("/api/messages/contact/c-mine")
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["data"]] == ["m-mine"]


@pytest.mark.parametrize("user", [REP, ADMIN])
async def test_demo_visitor_still_readable_and_markable(client, async_session_maker, user):
    """Task one's constraint: the inbox keeps demo conversations workable."""
    await _seed_conversations(async_session_maker)
    with logged_in_as(user):
        convo = await client.get("/api/messages/contact/c-demo")
        mark = await client.patch("/api/messages/m-demo/read")
    assert convo.status_code == 200
    assert [m["id"] for m in convo.json()["data"]] == ["m-demo"]
    assert mark.status_code == 200
    assert await _is_read(async_session_maker, "m-demo") is True
