"""CRM-TASK-027 acceptance tests for contact summary snapshots."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.models.autocount_document import AutocountDocument
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.task import Task
from app.models.user import User
from app.services.access_service import owner_scope
from app.services.ai_api_client import compact_json
from app.services.contact_summary_service import (
    SNAPSHOT_LIMIT_BYTES,
    _document_item,
    build_contact_snapshot,
)

NOW = datetime(2026, 10, 15, 10, 0)


def _user(user_id, role="sales", manager_id=None):
    return User(
        id=user_id,
        name=user_id,
        email=f"{user_id}@example.test",
        password_hash="hash",
        role=role,
        manager_id=manager_id,
    )


def _contact(**values):
    return Contact(id="contact", name="Contact", created_at=NOW, **values)


def _deal(deal_id, **values):
    fields = {
        "id": deal_id,
        "contact_id": "contact",
        "title": deal_id,
        "status": "lead",
        "updated_at": NOW,
    }
    fields.update(values)
    return Deal(**fields)


def _task(task_id, **values):
    fields = {"id": task_id, "contact_id": "contact", "title": task_id}
    fields.update(values)
    return Task(**fields)


def _document(document_id, **values):
    fields = {
        "id": document_id,
        "doc_type": "invoice",
        "doc_no": document_id,
        "customer_code": "customer",
        "contact_id": "contact",
    }
    fields.update(values)
    return AutocountDocument(**fields)


def _activity(activity_id, **values):
    fields = {
        "id": activity_id,
        "contact_id": "contact",
        "user_id": "sales",
        "deal_id": "deal",
        "type": "phone",
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
        "created_at": NOW,
    }
    fields.update(values)
    return Message(**fields)


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


@pytest.mark.asyncio
async def test_owner_scope_matches_owner_access_rules(async_session_maker):
    """Guards acceptance criterion 1: owner_scope is admin/team/self only."""
    admin, manager, sales, outsider = (
        _user("admin", "admin"),
        _user("manager", "manager"),
        _user("sales", manager_id="manager"),
        _user("outsider"),
    )
    await _seed(
        async_session_maker,
        admin,
        manager,
        sales,
        outsider,
        *[_deal(owner, assigned_to=owner) for owner in ("admin", "manager", "sales", "outsider")],
        _deal("unowned", assigned_to=None),
    )
    async with async_session_maker() as session:
        for user, expected in (
            (admin, {"admin", "manager", "sales", "outsider", "unowned"}),
            (manager, {"manager", "sales"}),
            (sales, {"sales"}),
            (outsider, {"outsider"}),
        ):
            ids = (
                (
                    await session.execute(
                        select(Deal.id).where(*(await owner_scope(session, user, Deal.assigned_to)))
                    )
                )
                .scalars()
                .all()
            )
            assert set(ids) == expected


@pytest.mark.asyncio
async def test_snapshot_visibility_and_exact_item_shapes(async_session_maker):
    """Guards criteria 2 and 3: owner filtering and the permitted field sets."""
    admin, manager, sales, other = (
        _user("admin", "admin"),
        _user("manager", "manager"),
        _user("sales", manager_id="manager"),
        _user("other"),
    )
    await _seed(
        async_session_maker,
        admin,
        manager,
        sales,
        other,
        _contact(company="Company", industry="Industry", last_contact=date(2026, 10, 1)),
        _deal("sales-deal", assigned_to="sales", amount=Decimal("1.2")),
        _deal("other-deal", assigned_to="other"),
        _deal("unowned-deal", assigned_to=None),
        _task("sales-task", assigned_to="sales"),
        _task("other-task", assigned_to="other"),
        _task("unowned-task", assigned_to=None),
        _document("invoice", outstanding_amount=Decimal("3"), due_date=date(2026, 10, 2)),
        _document("quote", doc_type="quotation", validity="30 days"),
        _activity("activity", content=None),
        _message("whatsapp", body=""),
        _message("email", channel="email", subject=None, body=""),
    )
    async with async_session_maker() as session:
        sales_snapshot = await build_contact_snapshot(session, user=sales, contact_id="contact")
        manager_snapshot = await build_contact_snapshot(session, user=manager, contact_id="contact")
        admin_snapshot = await build_contact_snapshot(session, user=admin, contact_id="contact")

    assert [item["title"] for item in sales_snapshot["deals"]] == ["sales-deal"]
    assert [item["title"] for item in sales_snapshot["open_tasks"]] == ["sales-task"]
    assert [item["title"] for item in manager_snapshot["deals"]] == ["sales-deal"]
    assert [item["title"] for item in manager_snapshot["open_tasks"]] == ["sales-task"]
    assert {item["title"] for item in admin_snapshot["deals"]} == {
        "sales-deal",
        "other-deal",
        "unowned-deal",
    }
    assert {item["title"] for item in admin_snapshot["open_tasks"]} == {
        "sales-task",
        "other-task",
        "unowned-task",
    }
    assert set(admin_snapshot["contact"]) == {
        "name",
        "company",
        "industry",
        "tags",
        "notes",
        "customer_since",
        "last_contact",
    }
    assert set(admin_snapshot["deals"][0]) == {
        "title",
        "status",
        "priority",
        "amount",
        "won_at",
        "updated_at",
    }
    assert set(admin_snapshot["open_tasks"][0]) == {"title", "priority", "due_date"}
    document_keys = {item["type"]: set(item) for item in admin_snapshot["documents"]}
    assert document_keys["invoice"] == {
        "type",
        "status",
        "currency",
        "total",
        "doc_date",
        "outstanding",
        "due_date",
    }
    assert document_keys["quotation"] == {
        "type",
        "status",
        "currency",
        "total",
        "doc_date",
        "validity",
    }
    other_document, other_document_truncated = _document_item(
        # Unsaved, so column defaults never apply: set the total explicitly.
        _document("other-doc", doc_type="other", total_amount=Decimal("0"))
    )
    assert set(other_document) == {"type", "status", "currency", "total", "doc_date"}
    assert other_document_truncated is False
    assert set(admin_snapshot["activities"][0]) == {"at", "type", "content"}
    messages_by_channel = {item["channel"]: item for item in admin_snapshot["messages"]}
    assert set(messages_by_channel["whatsapp"]) == {
        "at",
        "direction",
        "author_kind",
        "channel",
        "body",
    }
    assert set(messages_by_channel["email"]) == {
        "at",
        "direction",
        "author_kind",
        "channel",
        "body",
        "subject",
    }
    assert admin_snapshot["activities"][0]["content"] == ""
    assert messages_by_channel["whatsapp"]["body"] == ""
    assert messages_by_channel["email"]["subject"] is None
    assert admin_snapshot["deals"][0]["won_at"] is None
    assert document_keys["invoice"]
    assert (
        next(item for item in admin_snapshot["documents"] if item["type"] == "invoice")["doc_date"]
        is None
    )
    assert (
        next(item for item in admin_snapshot["documents"] if item["type"] == "quotation")[
            "validity"
        ]
        == "30 days"
    )
    assert admin_snapshot["contact"]["last_contact"] == "2026-10-01"


@pytest.mark.asyncio
async def test_snapshot_formats_and_ordering_limits(async_session_maker):
    """Guards criteria 4 and 5: wire formats, ordering, caps, and exclusions."""
    user = _user("sales")
    rows = [_contact(last_contact=None), user]
    rows += [
        _deal("deal-too-old", assigned_to="sales", updated_at=NOW),
        _deal("deal-tie-a", assigned_to="sales", updated_at=NOW + timedelta(seconds=100)),
        _deal("deal-tie-b", assigned_to="sales", updated_at=NOW + timedelta(seconds=100)),
        *[
            _deal(
                f"deal-{index:02}",
                assigned_to="sales",
                updated_at=NOW + timedelta(seconds=index),
            )
            for index in range(11, 19)
        ],
    ]
    rows += [_deal("deleted", assigned_to="sales", deleted_at=NOW + timedelta(days=1))]
    rows += [
        _task("task-tie-a", assigned_to="sales", due_date=date(2026, 10, 1)),
        _task("task-tie-b", assigned_to="sales", due_date=date(2026, 10, 1)),
        _task("task-02", assigned_to="sales", due_date=date(2026, 10, 2)),
        _task("task-03", assigned_to="sales", due_date=date(2026, 10, 3)),
        _task("task-null-a", assigned_to="sales", due_date=None),
        _task("task-null-b", assigned_to="sales", due_date=None),
    ]
    rows += [
        _task("done", assigned_to="sales", is_done=True),
        Task(id="other-contact", contact_id="other", title="other", assigned_to="sales"),
    ]
    rows += [
        _document("doc-tie-a", doc_date=date(2026, 10, 5), status="doc-tie-a"),
        _document("doc-tie-b", doc_date=date(2026, 10, 5), status="doc-tie-b"),
        _document("doc-04", doc_date=date(2026, 10, 4), status="doc-04"),
        _document("doc-03", doc_date=date(2026, 10, 3), status="doc-03"),
        _document("doc-null-a", doc_date=None, status="doc-null-a"),
        _document("doc-null-b", doc_date=None, status="doc-null-b"),
    ]
    rows += [
        AutocountDocument(
            id="other-document",
            doc_type="invoice",
            doc_no="other",
            customer_code="customer",
            contact_id="other",
            status="other-document",
        ),
    ]
    rows += [
        _activity(
            f"activity-{index:02}",
            follow_date=NOW + timedelta(seconds=index),
            content=f"activity-{index:02}",
        )
        for index in range(2, 15)
    ]
    rows += [
        _activity("activity-too-old", follow_date=NOW, content="activity-too-old"),
        _activity(
            "activity-tie-a", follow_date=NOW + timedelta(seconds=100), content="activity-tie-a"
        ),
        _activity(
            "activity-tie-b", follow_date=NOW + timedelta(seconds=100), content="activity-tie-b"
        ),
        *[
            _message(
                f"message-{index:02}",
                created_at=NOW + timedelta(seconds=index),
                body=f"message-{index:02}",
            )
            for index in range(1, 9)
        ],
        _message("message-too-old", created_at=NOW, body="message-too-old"),
        _message("message-tie-a", created_at=NOW + timedelta(seconds=100), body="message-tie-a"),
        _message("message-tie-b", created_at=NOW + timedelta(seconds=100), body="message-tie-b"),
    ]
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")

    assert [item["title"] for item in snapshot["deals"]] == [
        "deal-tie-b",
        "deal-tie-a",
        "deal-18",
        "deal-17",
        "deal-16",
        "deal-15",
        "deal-14",
        "deal-13",
        "deal-12",
        "deal-11",
    ]
    assert "deleted" not in {item["title"] for item in snapshot["deals"]}
    assert "deal-too-old" not in {item["title"] for item in snapshot["deals"]}
    assert [item["title"] for item in snapshot["open_tasks"]] == [
        "task-tie-a",
        "task-tie-b",
        "task-02",
        "task-03",
        "task-null-a",
    ]
    assert {item["title"] for item in snapshot["open_tasks"]}.isdisjoint(
        {"done", "other-contact", "task-null-b"}
    )
    assert [item["doc_date"] for item in snapshot["documents"]] == [
        "2026-10-05",
        "2026-10-05",
        "2026-10-04",
        "2026-10-03",
        None,
    ]
    assert [item["total"] for item in snapshot["documents"]] == ["0.00"] * 5
    assert [item["status"] for item in snapshot["documents"]] == [
        "doc-tie-b",
        "doc-tie-a",
        "doc-04",
        "doc-03",
        "doc-null-b",
    ]
    assert [item["content"] for item in snapshot["activities"]] == [
        *[f"activity-{index:02}" for index in range(2, 15)],
        "activity-tie-a",
        "activity-tie-b",
    ]
    assert "activity-too-old" not in {item["content"] for item in snapshot["activities"]}
    assert [item["body"] for item in snapshot["messages"]] == [
        *[f"message-{index:02}" for index in range(1, 9)],
        "message-tie-a",
        "message-tie-b",
    ]
    assert "message-too-old" not in {item["body"] for item in snapshot["messages"]}
    assert snapshot["contact"]["customer_since"] == "2026-10-15"
    assert snapshot["contact"]["last_contact"] is None
    assert snapshot["deals"][0]["amount"] == "0.00"
    assert snapshot["deals"][0]["updated_at"] == "2026-10-15T10:01:40Z"


@pytest.mark.asyncio
async def test_snapshot_field_and_count_truncation_flags(async_session_maker):
    """Guards criteria 6 and 7: inference and every field/count truncation trigger."""
    user = _user("sales")
    await _seed(
        async_session_maker,
        user,
        _contact(tags=["t" * 30], notes="n" * 500),
        _deal("deal", assigned_to="sales", title="d" * 100),
        _task("task", assigned_to="sales", title="t" * 100),
        _document("quote", doc_type="quotation", validity="v" * 100),
        _activity("activity", content="a" * 300),
        _message("inbound", author_kind=None, body="m" * 300),
        _message(
            "outbound", direction="outbound", author_kind=None, channel="email", subject="s" * 100
        ),
    )
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    assert snapshot["truncated"] is False
    assert snapshot["messages"][0]["author_kind"] == "customer"
    assert snapshot["messages"][1]["author_kind"] == "agent"

    async with async_session_maker() as session:
        session.add_all(
            [_deal(f"extra-deal-{index}", assigned_to="sales") for index in range(10)]
            + [_task(f"extra-task-{index}", assigned_to="sales") for index in range(5)]
            + [_document(f"extra-doc-{index}") for index in range(5)]
            + [_activity(f"extra-activity-{index}") for index in range(15)]
            + [_message(f"extra-message-{index}") for index in range(9)]
        )
        await session.commit()
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_snapshot_at_every_record_limit_is_not_truncated(async_session_maker):
    """Guards criterion 7: short records exactly at each cap do not set truncated."""
    user = _user("sales")
    await _seed(
        async_session_maker,
        user,
        _contact(),
        *[_deal(f"deal-{index}", assigned_to="sales") for index in range(10)],
        *[_task(f"task-{index}", assigned_to="sales") for index in range(5)],
        *[_document(f"document-{index}") for index in range(5)],
        *[_activity(f"activity-{index}") for index in range(15)],
        *[_message(f"message-{index}") for index in range(10)],
    )
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    assert snapshot["truncated"] is False
    assert [
        len(snapshot[key]) for key in ("deals", "open_tasks", "documents", "activities", "messages")
    ] == [
        10,
        5,
        5,
        15,
        10,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "eleven_tags",
        "long_tag",
        "long_notes",
        "long_deal_title",
        "long_task_title",
        "long_validity",
        "long_activity",
        "long_body",
        "long_subject",
        "extra_deal",
        "extra_task",
        "extra_document",
        "extra_activity",
        "extra_message",
    ],
)
async def test_each_snapshot_truncation_trigger_is_independent(async_session_maker, kind):
    """Guards criterion 7: each specified trigger alone sets truncated."""
    user = _user("sales")
    contact = _contact(tags=["t" * 30], notes="n" * 500)
    deal = _deal("deal", assigned_to="sales", title="d" * 100)
    task = _task("task", assigned_to="sales", title="t" * 100)
    document = _document("quote", doc_type="quotation", validity="v" * 100)
    activity = _activity("activity", content="a" * 300)
    message = _message("message", channel="email", body="m" * 300, subject="s" * 100)
    rows = [user, contact, deal, task, document, activity, message]
    if kind == "eleven_tags":
        contact.tags = ["tag"] * 11
    elif kind == "long_tag":
        contact.tags = ["t" * 31]
    elif kind == "long_notes":
        contact.notes = "n" * 501
    elif kind == "long_deal_title":
        deal.title = "d" * 101
    elif kind == "long_task_title":
        task.title = "t" * 101
    elif kind == "long_validity":
        document.validity = "v" * 101
    elif kind == "long_activity":
        activity.content = "a" * 301
    elif kind == "long_body":
        message.body = "m" * 301
    elif kind == "long_subject":
        message.subject = "s" * 101
    elif kind == "extra_deal":
        rows.extend(_deal(f"deal-{index}", assigned_to="sales") for index in range(10))
    elif kind == "extra_task":
        rows.extend(_task(f"task-{index}", assigned_to="sales") for index in range(5))
    elif kind == "extra_document":
        rows.extend(_document(f"document-{index}") for index in range(5))
    elif kind == "extra_activity":
        rows.extend(_activity(f"activity-{index}") for index in range(15))
    else:
        rows.extend(_message(f"message-{index}") for index in range(10))
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_snapshot_discards_only_oldest_messages_until_within_size_limit(async_session_maker):
    """Guards criterion 8a: message-only discards preserve all activities and newest messages."""
    user = _user("sales")
    await _seed(
        async_session_maker,
        user,
        _contact(),
        _activity("activity", content="activity"),
        *[
            _message(
                f"message-{index}",
                created_at=NOW + timedelta(seconds=index),
                body=f"{index}:" + "\x00" * 298,
            )
            for index in range(10)
        ],
    )
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    remaining = [int(item["body"].split(":", 1)[0]) for item in snapshot["messages"]]
    assert snapshot["truncated"] is True
    assert len(snapshot["activities"]) == 1
    assert 1 <= len(remaining) < 10
    assert remaining == list(range(10 - len(remaining), 10))


@pytest.mark.asyncio
async def test_snapshot_keeps_minimum_records_when_still_oversized_and_missing_contact_errors(
    async_session_maker,
):
    """Guards criteria 8b and 9: discard reaches newest minima and absent contact errors."""
    user = _user("sales")
    await _seed(
        async_session_maker,
        user,
        _contact(company="\x00" * 4000),
        *[
            _activity(
                f"activity-{index}",
                follow_date=NOW + timedelta(seconds=index),
                content=f"{index}:" + "\x00" * 298,
            )
            for index in range(15)
        ],
        *[
            _message(
                f"message-{index}",
                created_at=NOW + timedelta(seconds=index),
                body=f"{index}:" + "\x00" * 298,
            )
            for index in range(10)
        ],
    )
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
        with pytest.raises(ValueError):
            await build_contact_snapshot(session, user=user, contact_id="missing")
    assert len(snapshot["messages"]) == len(snapshot["activities"]) == 1
    assert snapshot["messages"][0]["body"].startswith("9:")
    assert snapshot["activities"][0]["content"].startswith("14:")
    assert len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_snapshot_allows_oversized_single_records_without_truncation(async_session_maker):
    """Guards criterion 8c: already-minimal oversized snapshots are not marked truncated."""
    user = _user("sales")
    await _seed(
        async_session_maker,
        user,
        _contact(company="\x00" * 4000),
        _activity("activity", content="a"),
        _message("message", body="m"),
    )
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    assert len(snapshot["messages"]) == len(snapshot["activities"]) == 1
    assert len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    assert snapshot["truncated"] is False
