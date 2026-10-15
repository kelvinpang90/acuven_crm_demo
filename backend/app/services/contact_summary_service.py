"""Customer-summary snapshots and AI orchestration."""

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
from app.services import access_service, ai_api_client
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    INVALID_INPUT,
    LANGUAGES,
    NO_HISTORY,
    NOT_CONFIGURED,
    OK,
    AiAssistResult,
    write_ai_request,
)
from app.services.ai_features import (
    MAX_TOKENS,
    SUMMARY_FEATURE,
    SUMMARY_SCHEMA,
    SUMMARY_SYSTEM,
    validate_summary,
)

SNAPSHOT_LIMIT_BYTES = 16 * 1024


def _truncate(value: str | None, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    return value[:limit], len(value) > limit


def _date(value: date | datetime | None) -> str | None:
    return (
        value.date().isoformat()
        if isinstance(value, datetime)
        else value.isoformat()
        if value
        else None
    )


def _time(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


def _money(value: Decimal) -> str:
    return format(Decimal(value), ".2f")


def _tags(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    tags = [str(tag)[:30] for tag in value[:10]]
    return tags, len(value) > 10 or any(len(str(tag)) > 30 for tag in value[:10])


async def _limited_rows(db: AsyncSession, query: Any, limit: int) -> tuple[list[Any], bool]:
    rows = (await db.execute(query.limit(limit + 1))).scalars().all()
    return rows[:limit], len(rows) > limit


async def build_contact_summary_snapshot(
    db: AsyncSession, *, user: User, contact_id: str
) -> dict[str, Any]:
    """Build the bounded summary input, without exposing employee identities."""
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise ValueError("contact not found")

    deal_scope = await access_service.owner_scope(db, user, Deal.assigned_to)
    task_scope = await access_service.owner_scope(db, user, Task.assigned_to)
    deals, deals_limited = await _limited_rows(
        db,
        select(Deal)
        .where(Deal.contact_id == contact_id, Deal.deleted_at.is_(None), *deal_scope)
        .order_by(Deal.updated_at.desc(), Deal.id.desc()),
        10,
    )
    tasks, tasks_limited = await _limited_rows(
        db,
        select(Task)
        .where(Task.contact_id == contact_id, Task.is_done.is_(False), *task_scope)
        .order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.asc()),
        5,
    )
    documents, documents_limited = await _limited_rows(
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
    activities, activities_limited = await _limited_rows(
        db,
        select(Activity)
        .where(Activity.contact_id == contact_id)
        .order_by(Activity.follow_date.desc(), Activity.id.desc()),
        15,
    )
    activities.reverse()
    messages, messages_limited = await _limited_rows(
        db,
        select(Message)
        .where(Message.contact_id == contact_id)
        .order_by(Message.created_at.desc(), Message.id.desc()),
        10,
    )
    messages.reverse()

    notes, notes_truncated = _truncate(contact.notes, 500)
    tags, tags_truncated = _tags(contact.tags)
    truncated = any(
        (
            notes_truncated,
            tags_truncated,
            deals_limited,
            tasks_limited,
            documents_limited,
            activities_limited,
            messages_limited,
        )
    )
    snapshot: dict[str, Any] = {
        "contact": {
            "name": contact.name,
            "company": contact.company,
            "industry": contact.industry,
            "tags": tags,
            "notes": notes,
            "customer_since": _date(contact.created_at),
            "last_contact": _date(contact.last_contact),
        },
        "deals": [],
        "open_tasks": [],
        "documents": [],
        "activities": [],
        "messages": [],
        "truncated": truncated,
    }
    for deal in deals:
        title, was_truncated = _truncate(deal.title or "", 100)
        snapshot["deals"].append(
            {
                "title": title or "",
                "status": deal.status,
                "priority": deal.priority,
                "amount": _money(deal.amount),
                "won_at": _date(deal.won_at),
                "updated_at": _time(deal.updated_at),
            }
        )
        truncated = truncated or was_truncated
    for task in tasks:
        title, was_truncated = _truncate(task.title, 100)
        snapshot["open_tasks"].append(
            {
                "title": title or "",
                "priority": task.priority,
                "due_date": _date(task.due_date),
            }
        )
        truncated = truncated or was_truncated
    for document in documents:
        item: dict[str, Any] = {
            "type": document.doc_type,
            "status": document.status,
            "currency": document.currency_code,
            "total": _money(document.total_amount),
            "doc_date": _date(document.doc_date),
        }
        if document.doc_type == "invoice":
            item.update(
                {
                    "outstanding": _money(document.outstanding_amount)
                    if document.outstanding_amount is not None
                    else None,
                    "due_date": _date(document.due_date),
                }
            )
        elif document.doc_type == "quotation":
            validity, was_truncated = _truncate(document.validity, 100)
            item["validity"] = validity
            truncated = truncated or was_truncated
        snapshot["documents"].append(item)
    for activity in activities:
        content, was_truncated = _truncate(activity.content or "", 300)
        snapshot["activities"].append(
            {"at": _time(activity.follow_date), "type": activity.type, "content": content or ""}
        )
        truncated = truncated or was_truncated
    for message in messages:
        body, body_truncated = _truncate(message.body or "", 300)
        item = {
            "at": _time(message.created_at),
            "direction": message.direction,
            "author_kind": message.author_kind
            or ("customer" if message.direction == "inbound" else "agent"),
            "channel": message.channel,
            "body": body or "",
        }
        truncated = truncated or body_truncated
        if message.channel == "email":
            subject, subject_truncated = _truncate(message.subject or "", 100)
            item["subject"] = subject or ""
            truncated = truncated or subject_truncated
        snapshot["messages"].append(item)
    snapshot["truncated"] = truncated
    while (
        len(snapshot["messages"]) > 1
        and len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    ):
        snapshot["messages"].pop(0)
        truncated = True
    while (
        len(snapshot["activities"]) > 1
        and len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    ):
        snapshot["activities"].pop(0)
        truncated = True
    snapshot["truncated"] = truncated
    return snapshot


async def generate_contact_summary(
    db: AsyncSession, *, user: User, contact_id: str, output_language: str
) -> AiAssistResult:
    """Generate one validated customer summary and audit every AI-client call."""
    if not isinstance(output_language, str) or output_language not in LANGUAGES:
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)
    snapshot = await build_contact_summary_snapshot(db, user=user, contact_id=contact_id)
    if not any(
        snapshot[block] for block in ("deals", "open_tasks", "documents", "activities", "messages")
    ):
        return AiAssistResult(NO_HISTORY)
    result = await ai_api_client.send_message(
        feature=SUMMARY_FEATURE,
        system=SUMMARY_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": compact_json(
                    {
                        "as_of": _time(datetime.utcnow()),
                        "output_language": output_language,
                        "snapshot": snapshot,
                    }
                ),
            }
        ],
        max_tokens=MAX_TOKENS,
        schema=SUMMARY_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_summary(result.output)
        if output is not None:
            await write_ai_request(
                db,
                user_id=user.id,
                contact_id=contact_id,
                feature=SUMMARY_FEATURE,
                request_id=result.request_id,
                outcome=OK,
            )
            return AiAssistResult(
                OK, request_id=result.request_id, truncated=snapshot["truncated"], **output
            )
        result = ai_api_client.AiApiResult(ERROR, request_id=result.request_id)
    await write_ai_request(
        db,
        user_id=user.id,
        contact_id=contact_id,
        feature=SUMMARY_FEATURE,
        request_id=result.request_id,
        outcome=result.outcome,
    )
    return AiAssistResult(
        result.outcome, request_id=result.request_id, truncated=snapshot["truncated"]
    )
