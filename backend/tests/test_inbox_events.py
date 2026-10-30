"""Inbox push: GET /api/messages/stream and the signal behind it.

Committed writes to `messages`, and to a contact's handover, owner or claim
columns, must reach open streams as `messages_changed`; other writes, and writes not yet committed, must not. The stream is driven
directly here: an endless HTTP stream would hang the ASGI test client.
"""

from datetime import datetime

import pytest
from sqlalchemy import update

from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_event_service, inbox_events
from tests._people import REP, logged_in_as

URL = "/api/messages/stream"
CHANGED = "event: messages_changed\ndata: {}\n\n"


def _message(**overrides) -> Message:
    fields = dict(
        channel="whatsapp",
        direction="inbound",
        sender_id="60111111111",
        recipient_id="crm",
        body="hello",
    )
    fields.update(overrides)
    return Message(**fields)


@pytest.fixture
def published(monkeypatch) -> list[None]:
    calls: list[None] = []
    monkeypatch.setattr(inbox_events, "publish", lambda: calls.append(None))
    return calls


async def test_publish_reaches_open_stream():
    stream = inbox_events.stream(heartbeat_seconds=5)
    try:
        assert await anext(stream) == ": connected\n\n"
        inbox_events.publish()
        assert await anext(stream) == CHANGED
    finally:
        await stream.aclose()
    assert inbox_events._subscribers == set()


async def test_pending_signals_collapse_into_one():
    stream = inbox_events.stream(heartbeat_seconds=0.05)
    try:
        await anext(stream)
        inbox_events.publish()
        inbox_events.publish()
        assert await anext(stream) == CHANGED
        assert await anext(stream) == ": heartbeat\n\n"
    finally:
        await stream.aclose()


async def test_stream_ends_after_max_seconds_and_unsubscribes():
    chunks = [c async for c in inbox_events.stream(heartbeat_seconds=0.01, max_seconds=0.05)]
    assert chunks[0] == ": connected\n\n"
    assert set(chunks[1:]) == {": heartbeat\n\n"}
    assert inbox_events._subscribers == set()


async def test_message_insert_publishes_on_commit_not_flush(async_session_maker, published):
    async with async_session_maker() as session:
        session.add(_message())
        await session.flush()
        assert published == []
        await session.commit()
    assert len(published) == 1


async def test_message_update_publishes(async_session_maker, published):
    async with async_session_maker() as session:
        msg = _message()
        session.add(msg)
        await session.commit()
        msg.is_read = True
        await session.commit()
    assert len(published) == 2


async def test_bulk_message_update_publishes(async_session_maker, published):
    async with async_session_maker() as session:
        await session.execute(update(Message).values(assigned_to="u-rep"))
        await session.commit()
    assert len(published) == 1


async def test_other_writes_do_not_publish(async_session_maker, published):
    async with async_session_maker() as session:
        session.add(
            User(id="u-x", name="X", email="x@example.com", password_hash="x", role="sales")
        )
        await session.commit()
    assert published == []


async def _saved_contact(session, **fields) -> Contact:
    contact = Contact(name="Alice", **fields)
    session.add(contact)
    await session.commit()
    return contact


# Criterion: a flushed change to a handover column publishes once, after commit only.
async def test_contact_handover_change_publishes_on_commit_not_flush(
    async_session_maker, published
):
    async with async_session_maker() as session:
        contact = await _saved_contact(session)
        contact.wa_handover_active = True
        await session.flush()
        assert published == []
        await session.commit()
    assert len(published) == 1


# Criterion: a flushed change to assigned_to publishes once, after commit only.
async def test_contact_assigned_to_change_publishes_on_commit_not_flush(
    async_session_maker, published
):
    async with async_session_maker() as session:
        contact = await _saved_contact(session)
        contact.assigned_to = "u-rep"
        await session.flush()
        assert published == []
        await session.commit()
    assert len(published) == 1


# Criterion: setting reply_claimed_by directly publishes once on commit.
async def test_contact_reply_claim_change_publishes(async_session_maker, published):
    async with async_session_maker() as session:
        contact = await _saved_contact(session)
        contact.reply_claimed_by = "u-rep"
        await session.commit()
    assert len(published) == 1


# Criterion: one commit publishes once however many changes it holds.
async def test_message_and_handover_in_one_commit_publish_once(async_session_maker, published):
    async with async_session_maker() as session:
        contact = await _saved_contact(session)
        session.add(_message())
        contact.wa_handover_active = True
        contact.wa_handover_updated_at = datetime(2026, 1, 1)
        await session.commit()
    assert len(published) == 1


# Criterion: changing only other contact columns (here the name) does not publish.
async def test_contact_name_change_does_not_publish(async_session_maker, published):
    async with async_session_maker() as session:
        contact = await _saved_contact(session)
        contact.name = "Bob"
        await session.commit()
    assert published == []


# Criterion: creating a contact does not publish on its own.
async def test_new_contact_does_not_publish(async_session_maker, published):
    async with async_session_maker() as session:
        await _saved_contact(session, assigned_to="u-rep", wa_handover_active=True)
    assert published == []


# Criterion: a bulk update(Contact) setting the handover columns, written the same
# way as the chatbot's synchronous response, publishes once.
async def test_bulk_contact_handover_update_publishes(async_session_maker, published):
    async with async_session_maker() as session:
        contact = await _saved_contact(session)
        result = await chatbot_event_service.write_response_handover(
            session,
            contact_id=contact.id,
            occurred_at=datetime(2026, 1, 1),
            active=True,
            expires_at=datetime(2026, 1, 2),
        )
        assert result == chatbot_event_service.STORED
        assert published == []
        await session.commit()
    assert len(published) == 1


# Criterion: a bulk update(Contact) that only sets other columns does not publish.
async def test_bulk_contact_bsuid_clear_does_not_publish(async_session_maker, published):
    async with async_session_maker() as session:
        await _saved_contact(session, whatsapp_bsuid="bsuid-1")
        await session.execute(
            update(Contact)
            .where(Contact.whatsapp_bsuid == "bsuid-1")
            .values(whatsapp_bsuid=None)
            .execution_options(synchronize_session="fetch")
        )
        await session.commit()
    assert published == []


async def test_stream_requires_login(client):
    res = await client.get(URL)
    assert res.status_code in (401, 403)


async def test_stream_response_is_unbuffered_event_stream(client, monkeypatch):
    async def one_chunk():
        yield ": connected\n\n"

    monkeypatch.setattr(inbox_events, "stream", one_chunk)
    with logged_in_as(REP):
        res = await client.get(URL)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["x-accel-buffering"] == "no"
    assert res.headers["cache-control"] == "no-cache"
    assert res.text == ": connected\n\n"
