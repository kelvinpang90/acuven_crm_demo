"""GET /api/messages/contact/{id} pages backwards from the newest message.

Without `before` it answers the latest `limit` messages; with `before` the
`limit` messages older than that one. Each page is oldest first. Messages
that share a timestamp are split by id, so paging never repeats or skips one.
"""

from datetime import datetime, timedelta

from app.models.message import Message
from tests._people import OTHER, REP, logged_in_as, seed_people

T0 = datetime(2026, 10, 1, 9, 0, 0)


def _message(index: int, created_at: datetime, contact_id: str = "c-mine") -> Message:
    return Message(
        id=f"m-{index:03d}",
        contact_id=contact_id,
        channel="whatsapp",
        direction="inbound",
        sender_id="60111111111",
        recipient_id="biz",
        body=f"message {index}",
        assigned_to="u-rep",
        created_at=created_at,
    )


async def _seed(session_maker, count: int, same_second: bool = False) -> None:
    await seed_people(session_maker)
    async with session_maker() as session:
        session.add_all(
            _message(i, T0 if same_second else T0 + timedelta(minutes=i)) for i in range(count)
        )
        await session.commit()


async def _page(client, **params) -> list[str]:
    with logged_in_as(REP):
        resp = await client.get("/api/messages/contact/c-mine", params=params)
    assert resp.status_code == 200
    return [m["id"] for m in resp.json()["data"]]


async def test_default_page_is_latest_fifty_oldest_first(client, async_session_maker):
    await _seed(async_session_maker, 60)
    assert await _page(client) == [f"m-{i:03d}" for i in range(10, 60)]


async def test_before_walks_back_without_gaps_or_repeats(client, async_session_maker):
    await _seed(async_session_maker, 25)
    seen: list[str] = []
    page = await _page(client, limit=10)
    while page:
        seen = page + seen
        page = await _page(client, limit=10, before=page[0])
    assert seen == [f"m-{i:03d}" for i in range(25)]


async def test_same_timestamp_split_by_id(client, async_session_maker):
    await _seed(async_session_maker, 7, same_second=True)
    first = await _page(client, limit=3)
    second = await _page(client, limit=3, before=first[0])
    third = await _page(client, limit=3, before=second[0])
    assert third + second + first == [f"m-{i:03d}" for i in range(7)]


async def test_cursor_from_another_customer_refused(client, async_session_maker):
    await _seed(async_session_maker, 3)
    async with async_session_maker() as session:
        session.add(_message(900, T0, contact_id="c-theirs"))
        await session.commit()
    with logged_in_as(REP):
        resp = await client.get("/api/messages/contact/c-mine", params={"before": "m-900"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_CURSOR"


async def test_limit_out_of_range_refused(client, async_session_maker):
    await _seed(async_session_maker, 3)
    with logged_in_as(REP):
        too_big = await client.get("/api/messages/contact/c-mine", params={"limit": 201})
        zero = await client.get("/api/messages/contact/c-mine", params={"limit": 0})
    assert too_big.status_code == 422
    assert zero.status_code == 422


async def test_paging_keeps_access_check(client, async_session_maker):
    await _seed(async_session_maker, 3)
    with logged_in_as(OTHER):
        resp = await client.get("/api/messages/contact/c-mine", params={"before": "m-002"})
    assert resp.status_code == 403
    assert "message 0" not in resp.text
