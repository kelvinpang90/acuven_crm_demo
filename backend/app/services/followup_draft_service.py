"""Business rules for AI-organized follow-up record drafts."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.deal import Deal
from app.models.task import Task
from app.models.user import User
from app.services import ai_api_client
from app.services.access_service import may_access_deal, owner_scope
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    INVALID_INPUT,
    LANGUAGES,
    NOT_CONFIGURED,
    OK,
    AiAssistResult,
    write_ai_request,
)
from app.services.ai_features import (
    FOLLOWUP_FEATURE,
    FOLLOWUP_SCHEMA,
    FOLLOWUP_SYSTEM,
    MAX_TOKENS,
    validate_followup_draft,
)


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
