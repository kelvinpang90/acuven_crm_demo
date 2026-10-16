"""Snapshot assembly for deal next-step suggestions."""

from datetime import datetime, timezone
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
from app.services.contact_summary_service import (
    SNAPSHOT_LIMIT_BYTES,
    _amount,
    _document_item,
    _limited_records,
    _tags,
    _truncate,
    _utc,
)

STALL_DAYS = 7


def _days_since(now: datetime, then: datetime) -> int:
    """Return whole elapsed UTC days, treating naive database timestamps as UTC."""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    else:
        then = then.astimezone(timezone.utc)
    return max(0, (now - then).days)


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


async def build_deal_snapshot(
    db: AsyncSession, *, user: User, deal: Deal, contact: Contact, now: datetime
) -> dict[str, Any]:
    """Build a bounded, model-safe snapshot for an already loaded deal and contact."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)

    task_scope = await owner_scope(db, user, Task.assigned_to)
    activities, activities_over_limit = await _limited_records(
        db,
        select(Activity)
        .where(Activity.deal_id == deal.id)
        .order_by(Activity.follow_date.desc(), Activity.id.desc()),
        15,
    )
    messages, messages_over_limit = await _limited_records(
        db,
        select(Message)
        .where(Message.contact_id == contact.id)
        .order_by(Message.created_at.desc(), Message.id.desc()),
        10,
    )
    tasks, tasks_over_limit = await _limited_records(
        db,
        select(Task)
        .where(Task.contact_id == contact.id, Task.is_done.is_(False), *task_scope)
        .order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.asc()),
        5,
    )
    documents, documents_over_limit = await _limited_records(
        db,
        select(AutocountDocument)
        .where(AutocountDocument.contact_id == contact.id)
        .order_by(
            AutocountDocument.doc_date.is_(None),
            AutocountDocument.doc_date.desc(),
            AutocountDocument.id.desc(),
        ),
        5,
    )
    latest_status_change = (
        await db.execute(
            select(Activity.follow_date)
            .where(Activity.deal_id == deal.id, Activity.type == "status change")
            .order_by(Activity.follow_date.desc(), Activity.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    latest_activity = activities[0].follow_date if activities else None
    latest_message = messages[0].created_at if messages else None
    idle_from = max(
        _as_utc(value)
        for value in (deal.created_at, latest_activity, latest_message)
        if value is not None
    )
    idle_days = _days_since(now, idle_from)
    days_in_status = _days_since(now, latest_status_change or deal.created_at)

    notes, truncated = _truncate(contact.notes, 500)
    tags, tags_truncated = _tags(contact.tags)
    title, title_truncated = _truncate(deal.title or "", 100)
    truncated = truncated or tags_truncated or title_truncated
    snapshot: dict[str, Any] = {
        "deal": {
            "title": title,
            "status": deal.status,
            "priority": deal.priority,
            "amount": _amount(deal.amount),
            "created_on": deal.created_at.date().isoformat(),
            "days_in_status": days_in_status,
            "idle_days": idle_days,
            "stalled": deal.status in {"lead", "following", "negotiating"}
            and idle_days >= STALL_DAYS,
        },
        "contact": {
            "name": contact.name,
            "company": contact.company,
            "industry": contact.industry,
            "tags": tags,
            "notes": notes,
        },
        "activities": [],
        "messages": [],
        "open_tasks": [],
        "documents": [],
        "truncated": False,
    }

    activities.reverse()
    messages.reverse()
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
    for task in tasks:
        title, title_truncated = _truncate(task.title or "", 100)
        snapshot["open_tasks"].append(
            {
                "title": title,
                "priority": task.priority,
                "due_date": task.due_date.isoformat() if task.due_date else None,
            }
        )
        truncated = truncated or title_truncated
    for document in documents:
        item, item_truncated = _document_item(document)
        snapshot["documents"].append(item)
        truncated = truncated or item_truncated

    snapshot["truncated"] = truncated or any(
        (activities_over_limit, messages_over_limit, tasks_over_limit, documents_over_limit)
    )
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
