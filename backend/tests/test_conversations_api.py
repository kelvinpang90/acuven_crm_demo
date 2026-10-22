"""Inbox grouped by customer: GET /api/messages/conversations and
PATCH /api/messages/contact/{id}/read.

One row per customer carrying their latest message and unread inbound count,
within the same reach as the message list; opening a conversation reads all of it.
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.message import Message
from tests._people import ADMIN, OTHER, REP, logged_in_as, seed_people

URL = "/api/messages/conversations"
T0 = datetime(2026, 10, 22, 12, 0)


def _msg(id, minutes, contact_id="c-mine", owner="u-rep", **fields) -> Message:
    values = dict(
        id=id,
        contact_id=contact_id,
        channel="whatsapp",
        direction="inbound",
        sender_id="60111111111",
        recipient_id="crm",
        body=id,
        assigned_to=owner,
        is_read=False,
        created_at=T0 + timedelta(minutes=minutes),
    )
    values.update(fields)
    return Message(**values)


async def _seed(session_maker, *messages: Message) -> None:
    await seed_people(session_maker)
    async with session_maker() as session:
        session.add_all(messages)
        await session.commit()


def _rows(resp):
    assert resp.status_code == 200, resp.text
    return [(r["id"], r["unread_count"]) for r in resp.json()["data"]["data"]]


async def test_one_row_per_customer_with_latest_message_and_unread_count(
    client, async_session_maker
):
    await _seed(
        async_session_maker,
        _msg("m1", 1),
        _msg("m2", 2),
        _msg("m3", 3, direction="outbound", sender_id="crm", recipient_id="60111111111"),
        _msg("t1", 4, contact_id="c-orphan", owner="u-rep", sender_id="60133333333"),
        _msg("m-read", 0, is_read=True),
    )
    with logged_in_as(ADMIN):
        resp = await client.get(URL)
    # Newest conversation first; outbound and already-read rows add no unread.
    assert _rows(resp) == [("t1", 1), ("m3", 2)]
    assert resp.json()["data"]["total"] == 2
    assert resp.json()["data"]["data"][1]["contact_name"] == "Mine"


async def test_messages_without_contact_group_by_the_far_end(client, async_session_maker):
    await _seed(
        async_session_maker,
        _msg("e1", 1, contact_id=None, channel="email", sender_id="a@example.com"),
        _msg(
            "e2",
            2,
            contact_id=None,
            channel="email",
            direction="outbound",
            sender_id="crm",
            recipient_id="a@example.com",
        ),
        _msg("e3", 3, contact_id=None, channel="email", sender_id="b@example.com"),
    )
    with logged_in_as(ADMIN):
        resp = await client.get(URL)
    assert _rows(resp) == [("e3", 1), ("e2", 1)]


async def test_rep_sees_only_their_own_customers(client, async_session_maker):
    await _seed(
        async_session_maker,
        _msg("mine", 1),
        _msg("theirs", 2, contact_id="c-theirs", owner="u-other", sender_id="60122222222"),
    )
    with logged_in_as(REP):
        mine = await client.get(URL)
    with logged_in_as(OTHER):
        theirs = await client.get(URL)
    assert _rows(mine) == [("mine", 1)]
    assert _rows(theirs) == [("theirs", 1)]


async def test_channel_and_unread_filters(client, async_session_maker):
    await _seed(
        async_session_maker,
        _msg("wa", 1),
        _msg("mail", 2, channel="email"),
        _msg("done", 3, contact_id="c-orphan", sender_id="60133333333", is_read=True),
    )
    with logged_in_as(ADMIN):
        whatsapp = await client.get(URL, params={"channel": "whatsapp"})
        unread = await client.get(URL, params={"unread": "true"})
        read = await client.get(URL, params={"unread": "false"})
    assert _rows(whatsapp) == [("done", 0), ("wa", 1)]
    assert _rows(unread) == [("mail", 2)]
    assert _rows(read) == [("done", 0)]


async def test_pagination_counts_conversations_not_messages(client, async_session_maker):
    await _seed(
        async_session_maker,
        *[_msg(f"m{i}", i) for i in range(3)],
        _msg("t1", 10, contact_id="c-orphan", sender_id="60133333333"),
    )
    with logged_in_as(ADMIN):
        first = await client.get(URL, params={"page": 1, "page_size": 1})
        second = await client.get(URL, params={"page": 2, "page_size": 1})
    assert first.json()["data"]["total"] == 2
    assert _rows(first) == [("t1", 1)]
    assert _rows(second) == [("m2", 3)]


async def test_opening_a_conversation_reads_all_its_inbound(client, async_session_maker):
    await _seed(
        async_session_maker,
        _msg("m1", 1),
        _msg("m2", 2),
        _msg("out", 3, direction="outbound", sender_id="crm", recipient_id="60111111111"),
        _msg("other", 4, contact_id="c-orphan", sender_id="60133333333"),
    )
    with logged_in_as(REP):
        resp = await client.patch("/api/messages/contact/c-mine/read")
    assert resp.status_code == 200
    assert resp.json()["data"]["updated"] == 2
    async with async_session_maker() as session:
        read = dict((await session.execute(select(Message.id, Message.is_read))).all())
    assert read == {"m1": True, "m2": True, "out": False, "other": False}


async def test_stranger_cannot_read_someone_elses_conversation(client, async_session_maker):
    await _seed(async_session_maker, _msg("m1", 1))
    with logged_in_as(OTHER):
        resp = await client.patch("/api/messages/contact/c-mine/read")
    assert resp.status_code == 403
    async with async_session_maker() as session:
        assert (await session.get(Message, "m1")).is_read is False
