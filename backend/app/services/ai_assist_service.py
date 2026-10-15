"""Database-side building blocks shared by inbox AI assistance features."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.user import User
from app.services import ai_api_client
from app.services.access_service import owner_scope
from app.services.ai_api_client import compact_json
from app.services.ai_features import (
    BRIEF_FEATURE,
    BRIEF_SCHEMA,
    BRIEF_SYSTEM,
    EMAIL_DRAFT_SCHEMA,
    MAX_TOKENS,
    REPLY_DRAFT_FEATURE,
    REPLY_DRAFT_SYSTEM,
    TRANSLATE_FEATURE,
    TRANSLATE_SCHEMA,
    TRANSLATE_SYSTEM,
    WHATSAPP_DRAFT_SCHEMA,
    validate_brief,
    validate_reply_draft,
    validate_translation,
)

SNAPSHOT_LIMIT_BYTES = 16 * 1024
LANGUAGES = {"zh-CN", "en", "ms"}
OK = "ok"
ERROR = "error"
UNAVAILABLE = "unavailable"
NOT_CONFIGURED = "not_configured"
NO_MESSAGES = "no_messages"
NO_HISTORY = "no_history"
INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class AiAssistResult:
    category: str
    request_id: str | None = None
    customer_need: str | None = None
    conversation_state: str | None = None
    customer_mood: str | None = None
    open_questions: list[str] | None = None
    suggested_next_step: str | None = None
    summary: str | None = None
    draft: str | None = None
    subject: str | None = None
    note_for_agent: str | None = None
    translation: str | None = None
    detected_language: str | None = None
    activity_type: str | None = None
    content: str | None = None
    tasks: list[dict[str, Any]] | None = None
    truncated: bool | None = None


def _truncate(value: str | None, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    return value[:limit], len(value) > limit


def _as_utc_string(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def _tags(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    tags = [str(tag)[:30] for tag in value[:10]]
    return tags, len(value) > 10 or any(len(str(tag)) > 30 for tag in value[:10])


def _snapshot_message(message: Message, now: datetime) -> tuple[dict[str, Any], bool]:
    body, truncated = _truncate(message.body, 2000)
    item: dict[str, Any] = {
        "at": _as_utc_string(message.created_at),
        "direction": message.direction,
        "author_kind": message.author_kind
        or ("customer" if message.direction == "inbound" else "agent"),
        "channel": message.channel,
        "body": body or "",
    }
    if message.channel == "email":
        subject, subject_truncated = _truncate(message.subject, 200)
        item["subject"] = subject or ""
        truncated = truncated or subject_truncated
    if message.channel == "whatsapp" and message.direction == "outbound":
        if message.author_kind == "agent" and message.external_id is None:
            item["delivery"] = (
                "sending" if now - message.created_at < timedelta(seconds=45) else "unconfirmed"
            )
        elif message.delivery_status:
            item["delivery"] = message.delivery_status
    return item, truncated


async def build_conversation_snapshot(
    db: AsyncSession, contact_id: str, *, user_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """Build the bounded, model-safe conversation snapshot for a contact."""
    now = now or datetime.utcnow()
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise ValueError("contact not found")

    user = await db.get(User, user_id)
    deals = []
    if user is not None:
        deals = (
            (
                await db.execute(
                    select(Deal)
                    .where(
                        Deal.contact_id == contact_id,
                        Deal.deleted_at.is_(None),
                        Deal.status.not_in(("won", "lost")),
                        *(await owner_scope(db, user, Deal.assigned_to)),
                    )
                    .order_by(Deal.updated_at.desc(), Deal.id.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
    messages = (
        (
            await db.execute(
                select(Message)
                .where(Message.contact_id == contact_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(40)
            )
        )
        .scalars()
        .all()
    )
    messages.reverse()

    notes, truncated = _truncate(contact.notes, 500)
    tags, tags_truncated = _tags(contact.tags)
    truncated = truncated or tags_truncated
    snapshot_messages = []
    for message in messages:
        item, item_truncated = _snapshot_message(message, now)
        snapshot_messages.append(item)
        truncated = truncated or item_truncated
    active = bool(
        contact.wa_handover_active
        and contact.wa_handover_expires_at
        and contact.wa_handover_expires_at > now
    )
    snapshot: dict[str, Any] = {
        "contact": {
            "name": contact.name,
            "company": contact.company,
            "industry": contact.industry,
            "tags": tags,
            "notes": notes,
        },
        "open_deals": [],
        "handover": {
            "active": active,
            "expires_at": _as_utc_string(contact.wa_handover_expires_at) if active else None,
        },
        "messages": snapshot_messages,
        "truncated": truncated,
    }
    for deal in deals:
        title = deal.title or ""
        snapshot["open_deals"].append(
            {
                "title": title[:100],
                "status": deal.status,
                "amount": format(Decimal(deal.amount), ".2f"),
            }
        )
        truncated = truncated or len(title) > 100
    snapshot["truncated"] = truncated
    while (
        len(snapshot["messages"]) > 1
        and len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    ):
        snapshot["messages"].pop(0)
        snapshot["truncated"] = True
    return snapshot


async def write_ai_request(
    db: AsyncSession,
    *,
    user_id: str,
    contact_id: str,
    feature: str,
    request_id: str | None,
    outcome: str,
    created_at: datetime | None = None,
) -> AiRequest:
    """Add exactly one minimal AI audit row; transaction ownership stays with caller."""
    request = AiRequest(
        user_id=user_id,
        contact_id=contact_id,
        feature=feature,
        request_id=request_id,
        outcome=outcome,
        created_at=created_at or datetime.utcnow(),
    )
    db.add(request)
    await db.flush()
    return request


def _as_of(now: datetime | None = None) -> str:
    return _as_utc_string(now or datetime.utcnow())


async def _record_result(
    db: AsyncSession,
    *,
    user_id: str,
    contact_id: str,
    feature: str,
    api_result: ai_api_client.AiApiResult,
) -> AiAssistResult:
    category = api_result.outcome
    await write_ai_request(
        db,
        user_id=user_id,
        contact_id=contact_id,
        feature=feature,
        request_id=api_result.request_id,
        outcome=category,
    )
    return AiAssistResult(category=category, request_id=api_result.request_id)


async def generate_conversation_brief(
    db: AsyncSession, *, user_id: str, contact_id: str, output_language: str
) -> AiAssistResult:
    """Generate a validated brief and audit every request which reaches the AI client."""
    if not isinstance(output_language, str) or output_language not in LANGUAGES:
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)
    snapshot = await build_conversation_snapshot(db, contact_id, user_id=user_id)
    if not snapshot["messages"]:
        return AiAssistResult(NO_MESSAGES)
    result = await ai_api_client.send_message(
        feature=BRIEF_FEATURE,
        system=BRIEF_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": compact_json(
                    {"as_of": _as_of(), "output_language": output_language, "snapshot": snapshot}
                ),
            }
        ],
        max_tokens=MAX_TOKENS,
        schema=BRIEF_SCHEMA,
    )
    if result.outcome != ai_api_client.OK:
        recorded = await _record_result(
            db, user_id=user_id, contact_id=contact_id, feature=BRIEF_FEATURE, api_result=result
        )
        return AiAssistResult(
            category=recorded.category,
            request_id=recorded.request_id,
            truncated=snapshot["truncated"],
        )
    output = validate_brief(result.output)
    if output is None:
        result = ai_api_client.AiApiResult(ERROR, request_id=result.request_id)
        recorded = await _record_result(
            db, user_id=user_id, contact_id=contact_id, feature=BRIEF_FEATURE, api_result=result
        )
        return AiAssistResult(
            ERROR, request_id=recorded.request_id, truncated=snapshot["truncated"]
        )
    await write_ai_request(
        db,
        user_id=user_id,
        contact_id=contact_id,
        feature=BRIEF_FEATURE,
        request_id=result.request_id,
        outcome=OK,
    )
    return AiAssistResult(
        OK, request_id=result.request_id, truncated=snapshot["truncated"], **output
    )


async def generate_reply_draft(
    db: AsyncSession,
    *,
    user_id: str,
    contact_id: str,
    channel: str,
    output_language: str,
    instruction: str | None = None,
) -> AiAssistResult:
    """Generate a validated channel-specific reply draft and audit the AI request."""
    if (
        not isinstance(output_language, str)
        or output_language not in LANGUAGES
        or not isinstance(channel, str)
        or channel not in {"whatsapp", "email"}
    ):
        return AiAssistResult(INVALID_INPUT)
    normalized_instruction = None
    if instruction is not None:
        if not isinstance(instruction, str):
            return AiAssistResult(INVALID_INPUT)
        normalized_instruction = instruction.strip()
        if not 1 <= len(normalized_instruction) <= 200:
            return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)
    snapshot = await build_conversation_snapshot(db, contact_id, user_id=user_id)
    if not snapshot["messages"]:
        return AiAssistResult(NO_MESSAGES)
    payload: dict[str, Any] = {
        "as_of": _as_of(),
        "output_language": output_language,
        "snapshot": snapshot,
        "channel": channel,
    }
    if normalized_instruction is not None:
        payload["instruction"] = normalized_instruction
    result = await ai_api_client.send_message(
        feature=REPLY_DRAFT_FEATURE,
        system=REPLY_DRAFT_SYSTEM,
        messages=[{"role": "user", "content": compact_json(payload)}],
        max_tokens=MAX_TOKENS,
        schema=WHATSAPP_DRAFT_SCHEMA if channel == "whatsapp" else EMAIL_DRAFT_SCHEMA,
    )
    if result.outcome != ai_api_client.OK:
        recorded = await _record_result(
            db,
            user_id=user_id,
            contact_id=contact_id,
            feature=REPLY_DRAFT_FEATURE,
            api_result=result,
        )
        return AiAssistResult(
            category=recorded.category,
            request_id=recorded.request_id,
            truncated=snapshot["truncated"],
        )
    output = validate_reply_draft(result.output, channel)
    if output is None:
        result = ai_api_client.AiApiResult(ERROR, request_id=result.request_id)
        recorded = await _record_result(
            db,
            user_id=user_id,
            contact_id=contact_id,
            feature=REPLY_DRAFT_FEATURE,
            api_result=result,
        )
        return AiAssistResult(
            ERROR, request_id=recorded.request_id, truncated=snapshot["truncated"]
        )
    await write_ai_request(
        db,
        user_id=user_id,
        contact_id=contact_id,
        feature=REPLY_DRAFT_FEATURE,
        request_id=result.request_id,
        outcome=OK,
    )
    return AiAssistResult(
        OK, request_id=result.request_id, truncated=snapshot["truncated"], **output
    )


async def translate_text(
    db: AsyncSession, *, user_id: str, contact_id: str, text: str, target_language: str
) -> AiAssistResult:
    """Translate normalized text and audit every request which reaches the AI client."""
    if not isinstance(text, str) or not isinstance(target_language, str):
        return AiAssistResult(INVALID_INPUT)
    normalized_text = text.strip()
    if target_language not in LANGUAGES or not 1 <= len(normalized_text) <= 2000:
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)
    result = await ai_api_client.send_message(
        feature=TRANSLATE_FEATURE,
        system=TRANSLATE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": compact_json(
                    {"text": normalized_text, "target_language": target_language}
                ),
            }
        ],
        max_tokens=MAX_TOKENS,
        schema=TRANSLATE_SCHEMA,
    )
    if result.outcome != ai_api_client.OK:
        return await _record_result(
            db, user_id=user_id, contact_id=contact_id, feature=TRANSLATE_FEATURE, api_result=result
        )
    output = validate_translation(result.output)
    if output is None:
        result = ai_api_client.AiApiResult(ERROR, request_id=result.request_id)
        return await _record_result(
            db, user_id=user_id, contact_id=contact_id, feature=TRANSLATE_FEATURE, api_result=result
        )
    await write_ai_request(
        db,
        user_id=user_id,
        contact_id=contact_id,
        feature=TRANSLATE_FEATURE,
        request_id=result.request_id,
        outcome=OK,
    )
    return AiAssistResult(OK, request_id=result.request_id, **output)
