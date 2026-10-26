"""Conversation tags and urgency order on GET /api/messages (CRM-TASK-068).

Each test names the acceptance criterion of CRM-TASK-068 it guards:
AC1 the triage field, AC2 the sort parameter, AC3 permissions, filters and paging unchanged.
"""

from datetime import datetime, timedelta

from app.models.conversation_triage import ConversationTriage
from app.models.message import Message
from tests._people import ADMIN, REP, logged_in_as, seed_people

START = datetime(2026, 10, 20, 8, 0, 0)


def _message(id_: str, contact_id, minute: int, *, is_read=False, assigned_to="u-rep") -> Message:
    return Message(
        id=id_,
        contact_id=contact_id,
        channel="whatsapp",
        direction="inbound",
        sender_id="60100000000",
        recipient_id="crm",
        body=id_,
        is_read=is_read,
        assigned_to=assigned_to,
        created_at=START + timedelta(minutes=minute),
    )


def _triage(contact_id: str, urgency: str, intent: str = "inquiry") -> ConversationTriage:
    return ConversationTriage(
        contact_id=contact_id,
        intent=intent,
        urgency=urgency,
        message_id="m-any",
        triaged_at=START,
    )


async def _seed_urgencies(maker) -> None:
    """Unread messages whose time order is the reverse of their urgency order, plus two read ones."""
    await seed_people(maker)
    async with maker() as session:
        session.add_all(
            [
                _triage("c-urgent", "urgent"),
                _triage("c-high", "high"),
                _triage("c-normal", "normal"),
                _triage("c-low", "low"),
                _message("m-urgent-old", "c-urgent", 1),
                _message("m-urgent-new", "c-urgent", 2),
                _message("m-high", "c-high", 3),
                _message("m-normal", "c-normal", 4),
                _message("m-low", "c-low", 5),
                _message("m-none", "c-untagged", 6),
                _message("m-read-urgent", "c-urgent", 7, is_read=True),
                _message("m-read-low", "c-low", 8, is_read=True),
            ]
        )
        await session.commit()


async def _ids(client, user, params: dict) -> list[str]:
    with logged_in_as(user):
        resp = await client.get("/api/messages", params=params)
    assert resp.status_code == 200
    return [m["id"] for m in resp.json()["data"]["data"]]


# --- AC1: triage ---


async def test_tagged_and_untagged_messages_carry_their_triage(client, async_session_maker):
    """AC1: a message whose customer has a conversation_triage row carries intent, urgency and
    triaged_at (UTC to the second, ending in Z); one without a row, or without a customer, null."""
    await seed_people(async_session_maker)
    async with async_session_maker() as session:
        session.add_all(
            [
                ConversationTriage(
                    contact_id="c-mine",
                    intent="order",
                    urgency="high",
                    message_id="m-tagged",
                    triaged_at=datetime(2026, 10, 20, 8, 30, 15, 123456),
                ),
                _message("m-tagged", "c-mine", 1),
                _message("m-untagged", "c-theirs", 2),
                _message("m-no-contact", None, 3),
            ]
        )
        await session.commit()

    with logged_in_as(ADMIN):
        resp = await client.get("/api/messages")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()["data"]["data"]}
    assert by_id["m-tagged"]["triage"] == {
        "intent": "order",
        "urgency": "high",
        "triaged_at": "2026-10-20T08:30:15Z",
    }
    assert by_id["m-untagged"]["triage"] is None
    assert by_id["m-no-contact"]["triage"] is None


# --- AC2: sort ---


async def test_unread_urgency_sort_orders_by_urgency_then_time(client, async_session_maker):
    """AC2: is_read=false and sort=urgency -> urgent, high, normal, low, untagged; the same
    urgency newest first."""
    await _seed_urgencies(async_session_maker)
    ids = await _ids(client, ADMIN, {"is_read": "false", "sort": "urgency"})
    assert ids == ["m-urgent-new", "m-urgent-old", "m-high", "m-normal", "m-low", "m-none"]


async def test_urgency_sort_outside_unread_stays_by_time(client, async_session_maker):
    """AC2: sort=urgency with is_read absent or true -> newest first, no error."""
    await _seed_urgencies(async_session_maker)
    ids = await _ids(client, ADMIN, {"sort": "urgency"})
    assert ids == [
        "m-read-low",
        "m-read-urgent",
        "m-none",
        "m-low",
        "m-normal",
        "m-high",
        "m-urgent-new",
        "m-urgent-old",
    ]
    ids = await _ids(client, ADMIN, {"is_read": "true", "sort": "urgency"})
    assert ids == ["m-read-low", "m-read-urgent"]


async def test_unknown_sort_is_rejected(client, async_session_maker):
    """AC2: a sort value other than time or urgency -> 422."""
    await seed_people(async_session_maker)
    with logged_in_as(ADMIN):
        resp = await client.get("/api/messages", params={"sort": "priority"})
    assert resp.status_code == 422


# --- AC3: permissions and paging ---


async def test_sales_sees_only_own_messages_with_correct_total(client, async_session_maker):
    """AC3: a sales rep pages through only their own messages, with the same total, whether
    sorted by time or by urgency."""
    await seed_people(async_session_maker)
    async with async_session_maker() as session:
        session.add_all(
            [
                _triage("c-mine", "urgent"),
                _triage("c-theirs", "urgent"),
                _message("m-mine-1", "c-mine", 1),
                _message("m-mine-2", "c-mine", 2),
                _message("m-mine-3", "c-orphan", 3),
                _message("m-theirs-1", "c-theirs", 4, assigned_to="u-other"),
                _message("m-theirs-2", "c-theirs", 5, assigned_to="u-other"),
            ]
        )
        await session.commit()

    mine = {"m-mine-1", "m-mine-2", "m-mine-3"}
    for sort in ("time", "urgency"):
        seen = []
        for page in (1, 2):
            params = {"is_read": "false", "sort": sort, "page": page, "page_size": 2}
            with logged_in_as(REP):
                resp = await client.get("/api/messages", params=params)
            assert resp.status_code == 200
            body = resp.json()["data"]
            assert body["total"] == 3
            seen.extend(m["id"] for m in body["data"])
        assert len(seen) == 3
        assert set(seen) == mine
