"""New fields on the message and contact endpoints (CRM-TASK-008, for CRM-TASK-010).

Each test names the acceptance criterion of CRM-TASK-008 it guards:
AC4 the message fields, AC5 the contact handover fields and phone_conflict.
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.contact import Contact
from app.models.message import Message
from app.services import contact_service
from tests._people import ADMIN, BOSS, REP, logged_in_as, seed_people

MESSAGE_FIELDS = ("author_kind", "bot_id", "delivery_status", "delivery_error")
FUTURE = datetime(2099, 1, 1, 0, 0, 0)
PAST = datetime(2000, 1, 1, 0, 0, 0)
UPDATED = datetime(2026, 10, 12, 8, 0, 0)
SHARED_PHONE = "60111111111"  # c-mine's number in tests._people


async def _seed_messages(maker) -> None:
    await seed_people(maker)
    async with maker() as session:
        session.add_all(
            [
                Message(
                    id="m-ai",
                    contact_id="c-mine",
                    channel="whatsapp",
                    direction="outbound",
                    sender_id="ai_chatbot_demo",
                    recipient_id=SHARED_PHONE,
                    body="Hi from the bot",
                    external_id="wamid.ai",
                    is_read=False,
                    assigned_to="u-rep",
                    author_kind="ai",
                    bot_id="bot-dental",
                    delivery_status="failed",
                    delivery_error="131026 Message undeliverable",
                ),
                Message(
                    id="m-old",
                    contact_id="c-mine",
                    channel="whatsapp",
                    direction="inbound",
                    sender_id=SHARED_PHONE,
                    recipient_id="crm",
                    body="from before the chatbot",
                    is_read=False,
                    assigned_to="u-rep",
                ),
            ]
        )
        await session.commit()


def _assert_message_fields(message: dict) -> None:
    for field in MESSAGE_FIELDS:
        assert field in message
    if message["id"] == "m-ai":
        assert message["author_kind"] == "ai"
        assert message["bot_id"] == "bot-dental"
        assert message["delivery_status"] == "failed"
        assert message["delivery_error"] == "131026 Message undeliverable"
    else:
        assert all(message[field] is None for field in MESSAGE_FIELDS)


# --- AC4: message fields ---


async def test_message_list_returns_new_fields(client, async_session_maker):
    """AC4: GET /api/messages -> every message carries the four new fields."""
    await _seed_messages(async_session_maker)
    with logged_in_as(ADMIN):
        resp = await client.get("/api/messages")
    assert resp.status_code == 200
    items = resp.json()["data"]["data"]
    assert {m["id"] for m in items} == {"m-ai", "m-old"}
    for message in items:
        _assert_message_fields(message)


async def test_contact_messages_return_new_fields(client, async_session_maker):
    """AC4: GET /api/messages/contact/{id} -> every message carries the four new fields."""
    await _seed_messages(async_session_maker)
    with logged_in_as(REP):
        resp = await client.get("/api/messages/contact/c-mine")
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert {m["id"] for m in items} == {"m-ai", "m-old"}
    for message in items:
        _assert_message_fields(message)


async def test_mark_read_returns_new_fields(client, async_session_maker):
    """AC4: PATCH /api/messages/{id}/read -> the message carries the four new fields."""
    await _seed_messages(async_session_maker)
    with logged_in_as(REP):
        resp = await client.patch("/api/messages/m-ai/read")
    assert resp.status_code == 200
    message = resp.json()["data"]
    assert message["is_read"] is True
    _assert_message_fields(message)


# --- AC5: contact handover fields ---


async def _load_contact(session, contact_id: str) -> Contact:
    query = select(Contact).where(Contact.id == contact_id)
    return (await session.execute(query)).scalar_one()


async def _set_handover(maker, contact_id: str, active: bool, expires_at) -> None:
    async with maker() as session:
        contact = await _load_contact(session, contact_id)
        contact.wa_handover_active = active
        contact.wa_handover_expires_at = expires_at
        contact.wa_handover_updated_at = UPDATED
        await session.commit()


async def _list_by_id(client, user) -> dict:
    with logged_in_as(user):
        resp = await client.get("/api/contacts")
    assert resp.status_code == 200
    return {c["id"]: c for c in resp.json()["data"]["data"]}


async def _detail(client, user, contact_id: str) -> dict:
    with logged_in_as(user):
        resp = await client.get(f"/api/contacts/{contact_id}")
    assert resp.status_code == 200
    return resp.json()["data"]


async def test_handover_in_effect_is_returned_with_utc_marks(client, async_session_maker):
    """AC5: an unexpired takeover -> active true, both times as UTC strings ending in Z,
    in the list and the detail."""
    await seed_people(async_session_maker)
    await _set_handover(async_session_maker, "c-mine", True, FUTURE)

    listed = (await _list_by_id(client, ADMIN))["c-mine"]
    detail = await _detail(client, ADMIN, "c-mine")
    for contact in (listed, detail):
        assert contact["wa_handover_active"] is True
        assert contact["wa_handover_expires_at"] == "2099-01-01T00:00:00Z"
        assert contact["wa_handover_updated_at"] == "2026-10-12T08:00:00Z"


@pytest.mark.parametrize(
    ("active", "expires_at"),
    [(True, PAST), (True, None), (False, None), (False, FUTURE)],
)
async def test_expired_or_inactive_handover_reads_false(
    client, async_session_maker, active, expires_at
):
    """AC5: marked active but expired or without an expiry, or not active -> false."""
    await seed_people(async_session_maker)
    await _set_handover(async_session_maker, "c-mine", active, expires_at)

    listed = (await _list_by_id(client, ADMIN))["c-mine"]
    detail = await _detail(client, ADMIN, "c-mine")
    for contact in (listed, detail):
        assert contact["wa_handover_active"] is False
        assert contact["wa_handover_updated_at"] == "2026-10-12T08:00:00Z"


async def test_contact_without_handover_has_empty_fields(client, async_session_maker):
    """AC5: a contact never taken over -> false and two nulls."""
    await seed_people(async_session_maker)
    detail = await _detail(client, ADMIN, "c-theirs")
    assert detail["wa_handover_active"] is False
    assert detail["wa_handover_expires_at"] is None
    assert detail["wa_handover_updated_at"] is None


async def test_bsuid_is_not_returned(client, async_session_maker):
    """AC5: whatsapp_bsuid is in neither the list nor the detail."""
    await seed_people(async_session_maker)
    async with async_session_maker() as session:
        contact = await _load_contact(session, "c-mine")
        contact.whatsapp_bsuid = "MY.visitor-1"
        await session.commit()

    listed = await _list_by_id(client, ADMIN)
    detail = await _detail(client, ADMIN, "c-mine")
    for contact in (*listed.values(), detail):
        assert "whatsapp_bsuid" not in contact
        assert "MY.visitor-1" not in contact.values()


# --- AC5: phone_conflict ---


async def _seed_conflict(maker) -> None:
    """c-dup shares c-mine's number; c-theirs and c-orphan have their own."""
    await seed_people(maker)
    async with maker() as session:
        session.add(Contact(id="c-dup", name="Dup", phone=SHARED_PHONE, assigned_to="u-other"))
        await session.commit()


async def test_admin_sees_phone_conflict(client, async_session_maker):
    """AC5: for an admin, contacts sharing a number are phone_conflict true in the list and
    the detail; the others false."""
    await _seed_conflict(async_session_maker)

    listed = await _list_by_id(client, ADMIN)
    assert listed["c-mine"]["phone_conflict"] is True
    assert listed["c-dup"]["phone_conflict"] is True
    assert listed["c-theirs"]["phone_conflict"] is False
    assert listed["c-orphan"]["phone_conflict"] is False

    assert (await _detail(client, ADMIN, "c-mine"))["phone_conflict"] is True
    assert (await _detail(client, ADMIN, "c-theirs"))["phone_conflict"] is False


@pytest.mark.parametrize("user", [REP, BOSS])
async def test_other_roles_always_see_false(client, async_session_maker, user):
    """AC5: sales and managers get phone_conflict false even on a conflicting contact."""
    await _seed_conflict(async_session_maker)

    listed = await _list_by_id(client, user)
    assert listed["c-mine"]["phone_conflict"] is False
    assert all(c["phone_conflict"] is False for c in listed.values())
    assert (await _detail(client, user, "c-mine"))["phone_conflict"] is False


async def test_deleting_the_duplicate_clears_the_conflict(client, async_session_maker):
    """AC5: nothing is stored; once the duplicate is deleted the conflict is gone."""
    await _seed_conflict(async_session_maker)
    async with async_session_maker() as session:
        dup = await _load_contact(session, "c-dup")
        dup.deleted_at = UPDATED
        await session.commit()

    assert (await _list_by_id(client, ADMIN))["c-mine"]["phone_conflict"] is False


async def test_list_looks_up_conflicts_in_one_query(client, async_session_maker, monkeypatch):
    """AC5: the list asks the CRM-TASK-007 conflict query once for the whole page, with every
    listed number; for other roles it is not asked at all."""
    await _seed_conflict(async_session_maker)
    calls = []
    original = contact_service.find_phone_conflicts

    async def _counting(db, phones):
        phones = list(phones)
        calls.append(phones)
        return await original(db, phones)

    monkeypatch.setattr(contact_service, "find_phone_conflicts", _counting)

    listed = await _list_by_id(client, ADMIN)
    assert len(calls) == 1
    assert sorted(p for p in calls[0] if p) == sorted(c["phone"] for c in listed.values())
    assert listed["c-mine"]["phone_conflict"] is True

    calls.clear()
    await _list_by_id(client, REP)
    assert calls == []
