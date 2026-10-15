"""CRM-TASK-035 acceptance tests for deal next-step snapshots."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.activity import Activity
from app.models.autocount_document import AutocountDocument
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.task import Task
from app.models.user import User
from app.services.deal_next_step_service import STALL_DAYS, build_deal_snapshot

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def _user(user_id="sales"):
    return User(
        id=user_id,
        name=user_id,
        email=f"{user_id}@example.test",
        password_hash="hash",
        role="sales",
    )


def _contact(contact_id="contact", **values):
    return Contact(id=contact_id, name="Contact", created_at=NOW.replace(tzinfo=None), **values)


def _deal(deal_id="deal", **values):
    fields = {
        "id": deal_id,
        "contact_id": "contact",
        "title": "Deal",
        "created_at": NOW.replace(tzinfo=None),
    }
    fields.update(values)
    return Deal(**fields)


def _activity(activity_id, **values):
    fields = {
        "id": activity_id,
        "contact_id": "contact",
        "user_id": "sales",
        "deal_id": "deal",
        "type": "phone",
        "follow_date": NOW.replace(tzinfo=None),
    }
    fields.update(values)
    return Activity(**fields)


def _message(message_id, **values):
    fields = {
        "id": message_id,
        "contact_id": "contact",
        "channel": "whatsapp",
        "direction": "inbound",
        "sender_id": "sender",
        "recipient_id": "recipient",
        "body": "body",
        "created_at": NOW.replace(tzinfo=None),
    }
    fields.update(values)
    return Message(**fields)


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _snapshot(maker, deal=None, contact=None):
    async with maker() as session:
        return await build_deal_snapshot(
            session, user=_user(), deal=deal or _deal(), contact=contact or _contact(), now=NOW
        )


@pytest.mark.asyncio
async def test_deal_and_contact_shape_and_field_truncation(async_session_maker):
    """Guards criterion 1: exact deal/contact shapes, formats, and truncation."""
    deal = _deal(title="x" * 101, amount=Decimal("12.3"), created_at=datetime(2026, 10, 1))
    contact = _contact(tags=["x" * 31] * 11, notes="n" * 501)
    await _seed(async_session_maker, _user(), deal, contact)
    snapshot = await _snapshot(async_session_maker, deal, contact)
    assert set(snapshot) == {
        "deal",
        "contact",
        "activities",
        "messages",
        "open_tasks",
        "documents",
        "truncated",
    }
    assert set(snapshot["deal"]) == {
        "title",
        "status",
        "priority",
        "amount",
        "created_on",
        "days_in_status",
        "idle_days",
        "stalled",
    }
    assert set(snapshot["contact"]) == {"name", "company", "industry", "tags", "notes"}
    assert snapshot["deal"]["title"] == "x" * 100
    assert snapshot["deal"]["amount"] == "12.30"
    assert snapshot["deal"]["created_on"] == "2026-10-01"
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], 10),
        ([_activity("recent", follow_date=(NOW - timedelta(days=3)).replace(tzinfo=None))], 3),
        ([_message("in", created_at=(NOW - timedelta(days=2)).replace(tzinfo=None))], 2),
        (
            [
                _message(
                    "out",
                    direction="outbound",
                    created_at=(NOW - timedelta(days=2)).replace(tzinfo=None),
                )
            ],
            2,
        ),
        ([_activity("future", follow_date=(NOW + timedelta(days=1)).replace(tzinfo=None))], 0),
        (
            [
                _activity(
                    "partial", follow_date=(NOW - timedelta(days=6, hours=23)).replace(tzinfo=None)
                )
            ],
            6,
        ),
    ],
)
async def test_idle_days_uses_latest_progress_and_floors_days(async_session_maker, rows, expected):
    """Guards criterion 2: idle_days uses all progress kinds and floors at zero."""
    deal = _deal(created_at=(NOW - timedelta(days=10)).replace(tzinfo=None))
    contact = _contact()
    await _seed(
        async_session_maker,
        _user(),
        deal,
        contact,
        *rows,
        _activity("other-deal", deal_id="other", follow_date=NOW.replace(tzinfo=None)),
        _message("other-contact", contact_id="other", created_at=NOW.replace(tzinfo=None)),
    )
    snapshot = await _snapshot(async_session_maker, deal, contact)
    assert snapshot["deal"]["idle_days"] == expected


@pytest.mark.asyncio
async def test_days_in_status_and_stalled_statuses(async_session_maker):
    """Guards criteria 3 and 4: status-change timing and the seven-day stalled rule."""
    deal = _deal(status="following", created_at=(NOW - timedelta(days=10)).replace(tzinfo=None))
    contact = _contact()
    await _seed(
        async_session_maker,
        _user(),
        deal,
        contact,
        _activity(
            "old", type="status change", follow_date=(NOW - timedelta(days=8)).replace(tzinfo=None)
        ),
        _activity(
            "new", type="status change", follow_date=(NOW - timedelta(days=3)).replace(tzinfo=None)
        ),
        _activity("phone", follow_date=(NOW - timedelta(days=1)).replace(tzinfo=None)),
    )
    snapshot = await _snapshot(async_session_maker, deal, contact)
    assert snapshot["deal"]["days_in_status"] == 3
    assert snapshot["deal"]["idle_days"] == 1
    assert snapshot["deal"]["stalled"] is False

    untouched = _deal("untouched", created_at=(NOW - timedelta(days=10)).replace(tzinfo=None))
    assert (await _snapshot(async_session_maker, untouched, contact))["deal"][
        "days_in_status"
    ] == 10

    for index, (status, idle, expected) in enumerate(
        (
            ("lead", 7, True),
            ("following", 6, False),
            ("negotiating", 7, True),
            ("won", 30, False),
            ("lost", 30, False),
        )
    ):
        candidate = _deal(
            f"candidate-{index}",
            status=status,
            created_at=(NOW - timedelta(days=idle)).replace(tzinfo=None),
        )
        result = await _snapshot(async_session_maker, candidate, contact)
        assert result["deal"]["stalled"] is expected
    assert STALL_DAYS == 7


@pytest.mark.asyncio
async def test_activities_messages_tasks_and_documents_are_bounded(async_session_maker):
    """Guards criteria 5-7: source isolation, order, formats, limits, and task scope."""
    deal, contact = _deal(), _contact()
    user, other = _user(), _user("other")
    activities = [
        _activity(f"{index:02}", follow_date=(NOW - timedelta(minutes=index)).replace(tzinfo=None))
        for index in range(16)
    ]
    messages = [
        _message(f"{index:02}", created_at=(NOW - timedelta(minutes=index)).replace(tzinfo=None))
        for index in range(11)
    ]
    tasks = [
        Task(id=f"task-{index}", contact_id="contact", title=f"Task {index}", assigned_to="sales")
        for index in range(6)
    ]
    documents = [
        AutocountDocument(
            id=f"doc-{index}",
            doc_type="invoice",
            doc_no=f"n-{index}",
            customer_code="customer",
            contact_id="contact",
        )
        for index in range(6)
    ]
    await _seed(
        async_session_maker,
        user,
        other,
        deal,
        contact,
        *activities,
        _activity("foreign", deal_id="other"),
        *messages,
        _message("foreign-message", contact_id="other"),
        *tasks,
        Task(id="done", contact_id="contact", title="done", assigned_to="sales", is_done=True),
        Task(id="other-task", contact_id="contact", title="other", assigned_to="other"),
        *documents,
    )
    snapshot = await _snapshot(async_session_maker, deal, contact)
    assert len(snapshot["activities"]) == 15
    assert [item["at"] for item in snapshot["activities"]] == sorted(
        item["at"] for item in snapshot["activities"]
    )
    assert len(snapshot["messages"]) == 10
    assert [item["at"] for item in snapshot["messages"]] == sorted(
        item["at"] for item in snapshot["messages"]
    )
    assert set(snapshot["activities"][0]) == {"at", "type", "content"}
    assert len(snapshot["open_tasks"]) == 5
    assert len(snapshot["documents"]) == 5
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_content_truncation_and_size_trimming_order(async_session_maker):
    """Guards criteria 5 and 8: content caps and 16 KiB removal order/minimum retention."""
    deal, contact = _deal(), _contact()
    emoji = chr(0x1F600) * 300
    messages = [
        _message(
            f"m{index}",
            body=emoji,
            created_at=(NOW - timedelta(minutes=20 - index)).replace(tzinfo=None),
        )
        for index in range(10)
    ]
    activities = [
        _activity(
            f"a{index}",
            content=emoji,
            follow_date=(NOW - timedelta(minutes=20 - index)).replace(tzinfo=None),
        )
        for index in range(15)
    ]
    await _seed(async_session_maker, _user(), deal, contact, *messages, *activities)
    snapshot = await _snapshot(async_session_maker, deal, contact)
    assert snapshot["truncated"] is True
    assert len(snapshot["messages"]) == 1
    assert len(snapshot["activities"]) == 1

    oversized = _activity("long", content="x" * 301)
    await _seed(async_session_maker, oversized)
    capped = await _snapshot(async_session_maker, deal, contact)
    assert all(len(item["content"]) <= 300 for item in capped["activities"])


@pytest.mark.asyncio
async def test_small_snapshot_is_not_truncated(async_session_maker):
    """Guards criterion 8: an unbounded small snapshot reports no truncation."""
    deal, contact = _deal(title=None), _contact(notes=None)
    await _seed(async_session_maker, _user(), deal, contact, _activity("empty", content=None))
    snapshot = await _snapshot(async_session_maker, deal, contact)
    assert snapshot["deal"]["title"] == ""
    assert snapshot["activities"][0]["content"] == ""
    assert snapshot["truncated"] is False
