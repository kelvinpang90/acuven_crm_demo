"""Business rules for AI-organized follow-up record drafts."""

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.task import Task
from app.models.user import User
from app.services import ai_api_client, ai_assist_service
from app.services.access_service import may_access_deal, owner_scope
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    INVALID_INPUT,
    LANGUAGES,
    NO_MESSAGES,
    NOT_CONFIGURED,
    OK,
    AiAssistResult,
    _as_utc_string,
    _snapshot_message,
    write_ai_request,
)
from app.services.ai_features import (
    FOLLOWUP_CHAT_FEATURE,
    FOLLOWUP_CHAT_SCHEMA,
    FOLLOWUP_CHAT_SYSTEM,
    FOLLOWUP_FEATURE,
    FOLLOWUP_SCHEMA,
    FOLLOWUP_SYSTEM,
    MAX_TOKENS,
    validate_followup_chat,
    validate_followup_draft,
)

CHAT_MESSAGE_LIMIT = 40


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _as_of() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _amount(value: Decimal) -> str:
    return format(Decimal(value), ".2f")


async def _open_tasks(db: AsyncSession, *, user: User, contact_id: str) -> list[dict[str, Any]]:
    tasks = (
        (
            await db.execute(
                select(Task)
                .where(
                    Task.contact_id == contact_id,
                    Task.is_done.is_(False),
                    *(await owner_scope(db, user, Task.assigned_to)),
                )
                .order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.id.asc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "title": (task.title or "")[:100],
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
        }
        for task in tasks
    ]


async def generate_followup_draft(
    db: AsyncSession,
    *,
    user: User,
    contact_id: str,
    deal_id: str,
    note: str,
    today: str,
    output_language: str,
) -> AiAssistResult:
    """Generate and validate an AI follow-up draft, auditing every client call."""
    contact = (
        await db.execute(
            select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if contact is None:
        raise ValueError("contact not found")

    if not isinstance(output_language, str) or output_language not in LANGUAGES:
        return AiAssistResult(INVALID_INPUT)
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
        return AiAssistResult(INVALID_INPUT)
    normalized_note = note.strip()
    today_date = _parse_date(today)
    if today_date is None or abs((today_date - datetime.utcnow().date()).days) > 1:
        return AiAssistResult(INVALID_INPUT)
    if not isinstance(deal_id, str):
        return AiAssistResult(INVALID_INPUT)
    deal = (
        await db.execute(
            select(Deal).where(
                Deal.id == deal_id,
                Deal.contact_id == contact_id,
                Deal.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if deal is None or not await may_access_deal(db, user, deal_id):
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)

    payload = {
        "as_of": _as_of(),
        "today": today,
        "output_language": output_language,
        "contact": {"name": contact.name, "company": contact.company},
        "deal": {
            "title": (deal.title or "")[:100],
            "status": deal.status,
            "amount": _amount(deal.amount),
        },
        "open_tasks": await _open_tasks(db, user=user, contact_id=contact_id),
        "note": normalized_note,
    }
    result = await ai_api_client.send_message(
        feature=FOLLOWUP_FEATURE,
        system=FOLLOWUP_SYSTEM,
        messages=[{"role": "user", "content": compact_json(payload)}],
        max_tokens=MAX_TOKENS,
        schema=FOLLOWUP_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_followup_draft(result.output, today)
        if output is not None:
            await write_ai_request(
                db,
                user_id=user.id,
                contact_id=contact_id,
                feature=FOLLOWUP_FEATURE,
                request_id=result.request_id,
                outcome=OK,
            )
            return AiAssistResult(OK, request_id=result.request_id, **output)
        outcome = ERROR
    else:
        outcome = result.outcome
    await write_ai_request(
        db,
        user_id=user.id,
        contact_id=contact_id,
        feature=FOLLOWUP_FEATURE,
        request_id=result.request_id,
        outcome=outcome,
    )
    return AiAssistResult(outcome, request_id=result.request_id)


def _local_time(value: datetime, utc_offset_minutes: int) -> str:
    offset = timedelta(minutes=utc_offset_minutes)
    return (value.replace(microsecond=0) + offset).replace(tzinfo=timezone(offset)).isoformat()


async def _chat_start(db: AsyncSession, contact_id: str) -> datetime | None:
    return (
        await db.execute(
            select(Activity.created_at)
            .where(Activity.contact_id == contact_id, Activity.type != "status change")
            .order_by(Activity.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _chat_messages(
    db: AsyncSession, contact_id: str, since: datetime | None
) -> list[Message]:
    conditions = [Message.contact_id == contact_id]
    if since is not None:
        conditions.append(Message.created_at > since)
    return list(
        (
            await db.execute(
                select(Message)
                .where(*conditions)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(CHAT_MESSAGE_LIMIT + 1)
            )
        )
        .scalars()
        .all()
    )


def _chat_activity_type(messages: list[dict[str, Any]]) -> str:
    channels = {message["channel"] for message in messages}
    if channels == {"whatsapp"}:
        return "WhatsApp"
    if channels == {"email"}:
        return "email"
    return "WhatsApp" if messages[-1]["channel"] == "whatsapp" else "email"


async def generate_followup_from_chat(
    db: AsyncSession,
    *,
    user: User,
    contact_id: str,
    deal_id: str,
    today: str,
    utc_offset_minutes: int,
    output_language: str,
) -> AiAssistResult:
    """Draft a follow-up from the messages since the last follow-up, auditing every client call."""
    contact = (
        await db.execute(
            select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if contact is None:
        raise ValueError("contact not found")

    if not isinstance(output_language, str) or output_language not in LANGUAGES:
        return AiAssistResult(INVALID_INPUT)
    today_date = _parse_date(today)
    if today_date is None or abs((today_date - datetime.utcnow().date()).days) > 1:
        return AiAssistResult(INVALID_INPUT)
    if (
        not isinstance(utc_offset_minutes, int)
        or isinstance(utc_offset_minutes, bool)
        or not -720 <= utc_offset_minutes <= 840
    ):
        return AiAssistResult(INVALID_INPUT)
    if not isinstance(deal_id, str):
        return AiAssistResult(INVALID_INPUT)
    deal = (
        await db.execute(
            select(Deal).where(
                Deal.id == deal_id,
                Deal.contact_id == contact_id,
                Deal.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if deal is None or not await may_access_deal(db, user, deal_id):
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)

    since = await _chat_start(db, contact_id)
    messages = await _chat_messages(db, contact_id, since)
    if not messages:
        return AiAssistResult(NO_MESSAGES)
    truncated = len(messages) > CHAT_MESSAGE_LIMIT
    messages = messages[:CHAT_MESSAGE_LIMIT]
    messages.reverse()
    now = datetime.utcnow()
    chat_messages = []
    for message in messages:
        item, item_truncated = _snapshot_message(message, now)
        item["at"] = _local_time(message.created_at, utc_offset_minutes)
        chat_messages.append(item)
        truncated = truncated or item_truncated

    payload: dict[str, Any] = {
        "as_of": _as_of(),
        "today": today,
        "output_language": output_language,
        "contact": {"name": contact.name, "company": contact.company},
        "deal": {
            "title": (deal.title or "")[:100],
            "status": deal.status,
            "amount": _amount(deal.amount),
        },
        "open_tasks": await _open_tasks(db, user=user, contact_id=contact_id),
        "since": _local_time(since, utc_offset_minutes) if since is not None else None,
        "messages": chat_messages,
        "truncated": truncated,
    }
    # Drop the oldest messages beyond 16 KiB but keep the newest one (design section 3 item 3).
    while (
        len(payload["messages"]) > 1
        and len(compact_json(payload).encode("utf-8")) > ai_assist_service.SNAPSHOT_LIMIT_BYTES
    ):
        payload["messages"].pop(0)
        payload["truncated"] = True
    activity_type = _chat_activity_type(payload["messages"])

    result = await ai_api_client.send_message(
        feature=FOLLOWUP_CHAT_FEATURE,
        system=FOLLOWUP_CHAT_SYSTEM,
        messages=[{"role": "user", "content": compact_json(payload)}],
        max_tokens=MAX_TOKENS,
        schema=FOLLOWUP_CHAT_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_followup_chat(result.output, today)
        if output is not None:
            await write_ai_request(
                db,
                user_id=user.id,
                contact_id=contact_id,
                feature=FOLLOWUP_CHAT_FEATURE,
                request_id=result.request_id,
                outcome=OK,
            )
            return AiAssistResult(
                OK,
                request_id=result.request_id,
                activity_type=activity_type,
                since=_as_utc_string(since) if since is not None else None,
                truncated=payload["truncated"],
                **output,
            )
        outcome = ERROR
    else:
        outcome = result.outcome
    await write_ai_request(
        db,
        user_id=user.id,
        contact_id=contact_id,
        feature=FOLLOWUP_CHAT_FEATURE,
        request_id=result.request_id,
        outcome=outcome,
    )
    return AiAssistResult(outcome, request_id=result.request_id)
