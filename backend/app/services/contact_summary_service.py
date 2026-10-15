"""Bounded customer snapshots for the contact AI summary feature."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.autocount_document import AutocountDocument
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.task import Task
from app.models.user import User
from app.services.access_service import owner_scope
from app.services.ai_api_client import compact_json

SNAPSHOT_LIMIT_BYTES = 16 * 1024


def _truncate(value: str | None, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    return value[:limit], len(value) > limit


def _tags(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    return (
        [str(tag)[:30] for tag in value[:10]],
        len(value) > 10 or any(len(str(tag)) > 30 for tag in value[:10]),
    )


def _date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None, microsecond=0).isoformat() + "Z"


def _amount(value: Decimal) -> str:
    return format(Decimal(value), ".2f")


async def _limited_records(db: AsyncSession, query: Any, limit: int) -> tuple[list[Any], bool]:
    records = (await db.execute(query.limit(limit + 1))).scalars().all()
    return records[:limit], len(records) > limit


def _document_item(document: AutocountDocument) -> tuple[dict[str, Any], bool]:
    item: dict[str, Any] = {
        "type": document.doc_type,
        "status": document.status,
        "currency": document.currency_code,
        "total": _amount(document.total_amount),
        "doc_date": _date(document.doc_date),
    }
    if document.doc_type == "invoice":
        item["outstanding"] = (
            _amount(document.outstanding_amount)
            if document.outstanding_amount is not None
            else None
        )
        item["due_date"] = _date(document.due_date)
    elif document.doc_type == "quotation":
        validity, truncated = _truncate(document.validity, 100)
        item["validity"] = validity
        return item, truncated
    return item, False


async def build_contact_snapshot(
    db: AsyncSession, *, user: User, contact_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """Build the internal, model-safe snapshot for one accessible contact."""
    del now
    contact = (
        await db.execute(
            select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if contact is None:
        raise ValueError("contact not found")

    deal_scope = await owner_scope(db, user, Deal.assigned_to)
    task_scope = await owner_scope(db, user, Task.assigned_to)
    deals, deals_over_limit = await _limited_records(
        db,
        select(Deal)
        .where(Deal.contact_id == contact_id, Deal.deleted_at.is_(None), *deal_scope)
        .order_by(Deal.updated_at.desc(), Deal.id.desc()),
        10,
    )
    tasks, tasks_over_limit = await _limited_records(
        db,
        select(Task)
        .where(Task.contact_id == contact_id, Task.is_done.is_(False), *task_scope)
        .order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.asc()),
        5,
    )
    documents, documents_over_limit = await _limited_records(
        db,
        select(AutocountDocument)
        .where(AutocountDocument.contact_id == contact_id)
        .order_by(
            AutocountDocument.doc_date.is_(None),
            AutocountDocument.doc_date.desc(),
            AutocountDocument.id.desc(),
        ),
        5,
    )
    activities, activities_over_limit = await _limited_records(
        db,
        select(Activity)
        .where(Activity.contact_id == contact_id)
        .order_by(Activity.follow_date.desc(), Activity.id.desc()),
        15,
    )
    messages, messages_over_limit = await _limited_records(
        db,
        select(Message)
        .where(Message.contact_id == contact_id)
        .order_by(Message.created_at.desc(), Message.id.desc()),
        10,
    )
    activities.reverse()
    messages.reverse()

    notes, truncated = _truncate(contact.notes, 500)
    tags, tags_truncated = _tags(contact.tags)
    truncated = truncated or tags_truncated
    snapshot: dict[str, Any] = {
        "contact": {
            "name": contact.name,
            "company": contact.company,
            "industry": contact.industry,
            "tags": tags,
            "notes": notes,
            "customer_since": _date(contact.created_at.date()),
            "last_contact": _date(contact.last_contact),
        },
        "deals": [],
        "open_tasks": [],
        "documents": [],
        "activities": [],
        "messages": [],
        "truncated": False,
    }

    for deal in deals:
        title, title_truncated = _truncate(deal.title or "", 100)
        snapshot["deals"].append(
            {
                "title": title,
                "status": deal.status,
                "priority": deal.priority,
                "amount": _amount(deal.amount),
                "won_at": _utc(deal.won_at) if deal.won_at else None,
                "updated_at": _utc(deal.updated_at),
            }
        )
        truncated = truncated or title_truncated
    for task in tasks:
        title, title_truncated = _truncate(task.title or "", 100)
        snapshot["open_tasks"].append(
            {"title": title, "priority": task.priority, "due_date": _date(task.due_date)}
        )
        truncated = truncated or title_truncated
    for document in documents:
        item, item_truncated = _document_item(document)
        truncated = truncated or item_truncated
        snapshot["documents"].append(item)
    for activity in activities:
        content, content_truncated = _truncate(activity.content or "", 300)
        snapshot["activities"].append(
            {"at": _utc(activity.follow_date), "type": activity.type, "content": content}
        )
        truncated = truncated or content_truncated
    for message in messages:
        body, body_truncated = _truncate(message.body or "", 300)
        item = {
            "at": _utc(message.created_at),
            "direction": message.direction,
            "author_kind": message.author_kind
            or ("customer" if message.direction == "inbound" else "agent"),
            "channel": message.channel,
            "body": body,
        }
        truncated = truncated or body_truncated
        if message.channel == "email":
            subject, subject_truncated = _truncate(message.subject, 100)
            item["subject"] = subject
            truncated = truncated or subject_truncated
        snapshot["messages"].append(item)

    truncated = truncated or any(
        (
            deals_over_limit,
            tasks_over_limit,
            documents_over_limit,
            activities_over_limit,
            messages_over_limit,
        )
    )
    snapshot["truncated"] = truncated
    while (
        len(snapshot["messages"]) > 1
        and len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    ):
        snapshot["messages"].pop(0)
        snapshot["truncated"] = True
    while (
        len(snapshot["activities"]) > 1
        and len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    ):
        snapshot["activities"].pop(0)
        snapshot["truncated"] = True
    return snapshot
