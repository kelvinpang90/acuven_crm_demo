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
from app.services.contact_summary_service import SNAPSHOT_LIMIT_BYTES, build_contact_snapshot

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
    fields = {"id": deal_id, "contact_id": "contact", "status": "lead", "updated_at": NOW}
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
    fields = {"id": activity_id, "contact_id": "contact", "user_id": "sales", "deal_id": "deal"}
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
        for user, expected in ((admin, 5), (manager, 2), (sales, 1), (outsider, 1)):
            ids = (
                (
                    await session.execute(
                        select(Deal.id).where(*(await owner_scope(session, user, Deal.assigned_to)))
                    )
                )
                .scalars()
                .all()
            )
            assert len(ids) == expected


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
        _document("other-doc", doc_type="other"),
        _activity("activity", content=None),
        _message("whatsapp", body=""),
        _message("email", channel="email", subject=None, body=""),
    )
    async with async_session_maker() as session:
        sales_snapshot = await build_contact_snapshot(session, user=sales, contact_id="contact")
        manager_snapshot = await build_contact_snapshot(session, user=manager, contact_id="contact")
        admin_snapshot = await build_contact_snapshot(session, user=admin, contact_id="contact")

    assert [item["title"] for item in sales_snapshot["deals"]] == ["sales-deal"]
    assert [item["title"] for item in manager_snapshot["open_tasks"]] == ["sales-task"]
    assert len(admin_snapshot["deals"]) == len(admin_snapshot["open_tasks"]) == 3
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
    assert document_keys["other"] == {"type", "status", "currency", "total", "doc_date"}
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
    assert admin_snapshot["contact"]["last_contact"] == "2026-10-01"


@pytest.mark.asyncio
async def test_snapshot_formats_and_ordering_limits(async_session_maker):
    """Guards criteria 4 and 5: wire formats, ordering, caps, and exclusions."""
    user = _user("sales")
    rows = [_contact(last_contact=None), user]
    rows += [
        _deal(f"deal-{index:02}", assigned_to="sales", updated_at=NOW + timedelta(seconds=index))
        for index in range(11)
    ]
    rows += [_deal("deleted", assigned_to="sales", deleted_at=NOW + timedelta(days=1))]
    rows += [
        _task(f"task-{index:02}", assigned_to="sales", due_date=date(2026, 10, index + 1))
        for index in range(6)
    ]
    rows += [
        _task("done", assigned_to="sales", is_done=True),
        Task(id="other-contact", contact_id="other", title="other", assigned_to="sales"),
    ]
    rows += [_document(f"doc-{index:02}", doc_date=date(2026, 10, index + 1)) for index in range(6)]
    rows += [
        _document("no-date", doc_date=None),
        AutocountDocument(
            id="other-document",
            doc_type="invoice",
            doc_no="other",
            customer_code="customer",
            contact_id="other",
        ),
    ]
    rows += [
        _activity(f"activity-{index:02}", follow_date=NOW + timedelta(seconds=index))
        for index in range(16)
    ]
    rows += [
        _message(f"message-{index:02}", created_at=NOW + timedelta(seconds=index))
        for index in range(11)
    ]
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")

    assert len(snapshot["deals"]) == 10 and snapshot["deals"][0]["title"] == "deal-10"
    assert len(snapshot["open_tasks"]) == 5 and snapshot["open_tasks"][-1]["title"] == "task-04"
    assert len(snapshot["documents"]) == 5 and snapshot["documents"][0]["doc_date"] == "2026-10-06"
    assert [item["content"] for item in snapshot["activities"]] == ["" for _ in range(15)]
    assert [item["body"] for item in snapshot["messages"]] == ["body" for _ in range(10)]
    assert snapshot["contact"]["customer_since"] == "2026-10-15"
    assert snapshot["contact"]["last_contact"] is None
    assert snapshot["deals"][0]["amount"] == "0.00"
    assert snapshot["deals"][0]["updated_at"].endswith("Z")


@pytest.mark.asyncio
async def test_snapshot_truncation_flags_and_size_discard(async_session_maker):
    """Guards criteria 6 to 8: inference, field/count flags, and 16 KiB discard order."""
    user = _user("sales")
    await _seed(
        async_session_maker,
        user,
        _contact(tags=["tag"] * 11),
        _deal("deal", assigned_to="sales", title="d" * 101),
        _task("task", assigned_to="sales", title="t" * 101),
        _document("quote", doc_type="quotation", validity="v" * 101),
        _activity("activity", content="a" * 301),
        _message("inbound", author_kind=None, body="m" * 301),
        _message(
            "outbound", direction="outbound", author_kind=None, channel="email", subject="s" * 101
        ),
    )
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    assert snapshot["truncated"] is True
    assert snapshot["messages"][0]["author_kind"] == "customer"
    assert snapshot["messages"][1]["author_kind"] == "agent"

    async with async_session_maker() as session:
        contact = await session.get(Contact, "contact")
        contact.tags = []
        contact.notes = "\x00" * 500
        session.add_all(
            [
                _message(
                    f"large-{index}", created_at=NOW + timedelta(minutes=index), body="x" * 300
                )
                for index in range(10)
            ]
        )
        session.add_all(
            [
                _activity(
                    f"large-activity-{index}",
                    follow_date=NOW + timedelta(minutes=index),
                    content="\x00" * 300,
                )
                for index in range(15)
            ]
        )
        await session.commit()
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
    assert snapshot["truncated"] is True
    assert len(snapshot["messages"]) >= 1 and snapshot["messages"][-1]["body"] == "x" * 300
    assert len(snapshot["activities"]) >= 1


@pytest.mark.asyncio
async def test_snapshot_keeps_minimum_records_when_still_oversized_and_missing_contact_errors(
    async_session_maker,
):
    """Guards criteria 8b/c and 9: oversized minimum is returned and absent contact errors."""
    user = _user("sales")
    await _seed(
        async_session_maker,
        user,
        _contact(company="\x00" * 4000),
        _activity("activity", content="\x00" * 300),
        _message("message", body="\x00" * 300),
    )
    async with async_session_maker() as session:
        snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
        with pytest.raises(ValueError):
            await build_contact_snapshot(session, user=user, contact_id="missing")
    assert len(snapshot["messages"]) == len(snapshot["activities"]) == 1
    assert len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    assert snapshot["truncated"] is False
