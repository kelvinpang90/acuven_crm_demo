"""Conversation tags and urgency order on GET /api/messages/conversations (CRM-TASK-074).

Each test names the acceptance criterion of CRM-TASK-074 it guards:
AC1 the triage field, AC2 the sort parameter, AC3 permissions, filters, unread_count and paging
unchanged.
"""

from datetime import datetime, timedelta

from app.models.conversation_triage import ConversationTriage
from app.models.message import Message
from tests._people import ADMIN, REP, logged_in_as, seed_people

URL = "/api/messages/conversations"
START = datetime(2026, 10, 20, 8, 0, 0)


def _msg(id_: str, contact_id, minute: int, *, owner="u-rep", **fields) -> Message:
    values = dict(
        id=id_,
        contact_id=contact_id,
        channel="whatsapp",
        direction="inbound",
        sender_id=f"60{id_}",
        recipient_id="crm",
        body=id_,
        is_read=False,
        assigned_to=owner,
        created_at=START + timedelta(minutes=minute),
    )
    values.update(fields)
    return Message(**values)


def _triage(contact_id: str, urgency: str, intent: str = "inquiry") -> ConversationTriage:
    return ConversationTriage(
        contact_id=contact_id,
        intent=intent,
        urgency=urgency,
        message_id="m-any",
        triaged_at=START,
    )


async def _seed(maker, *rows) -> None:
    await seed_people(maker)
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _seed_urgencies(maker) -> None:
    """Unread conversations whose time order is the reverse of their urgency order, two of them
    urgent, plus an untagged one, one without a contact and one already read."""
    await _seed(
        maker,
        _triage("c-urgent-a", "urgent"),
        _triage("c-urgent-b", "urgent"),
        _triage("c-high", "high"),
        _triage("c-normal", "normal"),
        _triage("c-low", "low"),
        _triage("c-read", "urgent"),
        _msg("urgent-a", "c-urgent-a", 1),
        _msg("urgent-b", "c-urgent-b", 2),
        _msg("high", "c-high", 3),
        _msg("normal", "c-normal", 4),
        _msg("low", "c-low", 5),
        _msg("untagged", "c-untagged", 6),
        _msg("no-contact", None, 7),
        _msg("read", "c-read", 8, is_read=True),
    )


async def _ids(client, params: dict) -> list[str]:
    with logged_in_as(ADMIN):
        resp = await client.get(URL, params=params)
    assert resp.status_code == 200, resp.text
    return [r["id"] for r in resp.json()["data"]["data"]]


# --- AC1: triage ---


async def test_tagged_untagged_and_contactless_rows_carry_their_triage(client, async_session_maker):
    """AC1: a row whose contact_id has a conversation_triage row carries intent, urgency and
    triaged_at (UTC to the second, ending in Z); one without a row, or without a contact, null."""
    await _seed(
        async_session_maker,
        ConversationTriage(
            contact_id="c-mine",
            intent="order",
            urgency="high",
            message_id="m-tagged",
            triaged_at=datetime(2026, 10, 20, 8, 30, 15, 123456),
        ),
        _msg("tagged", "c-mine", 1),
        _msg("untagged", "c-theirs", 2, owner="u-other"),
        _msg("no-contact", None, 3),
    )
    with logged_in_as(ADMIN):
        resp = await client.get(URL)
    assert resp.status_code == 200
    by_id = {r["id"]: r for r in resp.json()["data"]["data"]}
    assert by_id["tagged"]["triage"] == {
        "intent": "order",
        "urgency": "high",
        "triaged_at": "2026-10-20T08:30:15Z",
    }
    assert by_id["untagged"]["triage"] is None
    assert by_id["no-contact"]["triage"] is None


# --- AC2: sort ---


async def test_unread_urgency_sort_orders_by_urgency_then_time(client, async_session_maker):
    """AC2: unread=true and sort=urgency -> urgent, high, normal, low, untagged; the same
    urgency newest first."""
    await _seed_urgencies(async_session_maker)
    ids = await _ids(client, {"unread": "true", "sort": "urgency"})
    assert ids == ["urgent-b", "urgent-a", "high", "normal", "low", "no-contact", "untagged"]


async def test_urgency_sort_outside_unread_stays_by_time(client, async_session_maker):
    """AC2: sort=urgency with unread absent or false -> newest first, no error."""
    await _seed_urgencies(async_session_maker)
    ids = await _ids(client, {"sort": "urgency"})
    assert ids == [
        "read",
        "no-contact",
        "untagged",
        "low",
        "normal",
        "high",
        "urgent-b",
        "urgent-a",
    ]
    assert await _ids(client, {"unread": "false", "sort": "urgency"}) == ["read"]


async def test_unknown_sort_is_rejected(client, async_session_maker):
    """AC2: a sort value other than time or urgency -> 422."""
    await seed_people(async_session_maker)
    with logged_in_as(ADMIN):
        resp = await client.get(URL, params={"sort": "priority"})
    assert resp.status_code == 422


# --- AC3: permissions, unread_count and paging ---


async def test_sales_sees_only_own_conversations_with_correct_counts(client, async_session_maker):
    """AC3: a sales rep pages through only their own conversations, with the right unread_count
    and total, whether sorted by time or by urgency."""
    await _seed(
        async_session_maker,
        _triage("c-mine", "low"),
        _triage("c-theirs", "urgent"),
        _msg("mine-1", "c-mine", 1),
        _msg("mine-2", "c-mine", 2),
        _msg("mine-out", "c-mine", 3, direction="outbound", sender_id="crm", recipient_id="601"),
        _msg("orphan", "c-orphan", 4),
        _msg("no-contact", None, 5),
        _msg("theirs-1", "c-theirs", 6, owner="u-other"),
        _msg("theirs-2", "c-theirs", 7, owner="u-other"),
    )

    expected = {"mine-out": 2, "orphan": 1, "no-contact": 1}
    for sort in ("time", "urgency"):
        seen = []
        for page in (1, 2):
            params = {"unread": "true", "sort": sort, "page": page, "page_size": 2}
            with logged_in_as(REP):
                resp = await client.get(URL, params=params)
            assert resp.status_code == 200
            body = resp.json()["data"]
            assert body["total"] == 3
            seen.extend((r["id"], r["unread_count"]) for r in body["data"])
        assert len(seen) == 3
        assert dict(seen) == expected
