"""Inbox push: GET /api/messages/stream and the signal behind it.

Committed writes to `messages` must reach open streams as `messages_changed`;
other writes, and writes not yet committed, must not. The stream is driven
directly here: an endless HTTP stream would hang the ASGI test client.
"""

import pytest
from sqlalchemy import update

from app.models.message import Message
from app.models.user import User
from app.services import inbox_events
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
