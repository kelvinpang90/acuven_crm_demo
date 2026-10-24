"""Extracting contact details from chat messages (docs/contact-ai-extract.md)."""

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import ai_api_client
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    INVALID_INPUT,
    LANGUAGES,
    NO_MESSAGES,
    NOT_CONFIGURED,
    NOTHING_TO_FILL,
    OK,
    AiAssistResult,
    write_ai_request,
)
from app.services.ai_features import (
    CONTACT_EXTRACT_FEATURE,
    CONTACT_EXTRACT_SCHEMA,
    CONTACT_EXTRACT_SYSTEM,
    INDUSTRIES,  # noqa: F401  re-exported; defined next to the prompt that lists it
    MAX_TOKENS,
    validate_contact_extract,
)

EXTRACT_FIELDS = ("company", "industry", "email", "phone", "address")

SNAPSHOT_LIMIT_BYTES = 16 * 1024
MESSAGE_LIMIT = 40

_PHONE_NAME = re.compile(r"[0-9+ \-]+")
_DIGIT = re.compile(r"[0-9]")


def _truncate(value: str | None, limit: int) -> tuple[str, bool]:
    value = value or ""
    return value[:limit], len(value) > limit


def _utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None, microsecond=0).isoformat() + "Z"


def _name_is_phone(name: str | None) -> bool:
    stripped = (name or "").strip()
    return bool(stripped and _PHONE_NAME.fullmatch(stripped) and _DIGIT.search(stripped))


def _empty_fields(contact: Contact) -> list[str]:
    empty = []
    for field in EXTRACT_FIELDS:
        value = getattr(contact, field)
        if value is None or not str(value).strip():
            empty.append(field)
    return empty


def _message_item(message: Message) -> tuple[dict[str, Any], bool]:
    body, truncated = _truncate(message.body, 1000)
    item: dict[str, Any] = {
        "index": 0,
        "at": _utc(message.created_at),
        "direction": message.direction,
        "author_kind": message.author_kind
        or ("customer" if message.direction == "inbound" else "agent"),
        "channel": message.channel,
        "body": body,
    }
    if message.channel == "email":
        subject, subject_truncated = _truncate(message.subject, 100)
        item["subject"] = subject
        truncated = truncated or subject_truncated
    return item, truncated


def _reindex(messages: list[dict[str, Any]]) -> None:
    for index, item in enumerate(messages):
        item["index"] = index


async def build_extract_snapshot(
    db: AsyncSession, *, contact: Contact, now: datetime
) -> dict[str, Any]:
    """Build the model-facing snapshot for one already-loaded, undeleted contact."""
    # Messages are not filtered by now, the same as the contact summary.
    del now
    records = (
        (
            await db.execute(
                select(Message)
                .where(Message.contact_id == contact.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(MESSAGE_LIMIT + 1)
            )
        )
        .scalars()
        .all()
    )
    truncated = len(records) > MESSAGE_LIMIT
    records = list(records[:MESSAGE_LIMIT])
    records.reverse()

    name, name_truncated = _truncate(contact.name, 100)
    truncated = truncated or name_truncated
    messages = []
    for record in records:
        item, item_truncated = _message_item(record)
        messages.append(item)
        truncated = truncated or item_truncated
    _reindex(messages)

    snapshot: dict[str, Any] = {
        "contact": {
            "name": name,
            "name_is_phone": _name_is_phone(contact.name),
            "empty_fields": _empty_fields(contact),
        },
        "messages": messages,
        "truncated": truncated,
    }
    while (
        len(snapshot["messages"]) > 1
        and len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    ):
        snapshot["messages"].pop(0)
        _reindex(snapshot["messages"])
        snapshot["truncated"] = True
    return snapshot


async def generate_contact_extract(
    db: AsyncSession, *, user: User, contact_id: str, output_language: str
) -> AiAssistResult:
    """Generate validated contact-detail suggestions; never modifies the contact."""
    contact = (
        await db.execute(
            select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if contact is None:
        raise ValueError("contact not found")
    if not isinstance(output_language, str) or output_language not in LANGUAGES:
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)
    if not _empty_fields(contact) and not _name_is_phone(contact.name):
        return AiAssistResult(NOTHING_TO_FILL)
    now = datetime.now(timezone.utc)
    snapshot = await build_extract_snapshot(db, contact=contact, now=now)
    if not any(message["direction"] == "inbound" for message in snapshot["messages"]):
        return AiAssistResult(NO_MESSAGES)
    result = await ai_api_client.send_message(
        feature=CONTACT_EXTRACT_FEATURE,
        system=CONTACT_EXTRACT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": compact_json(
                    {"as_of": _utc(now), "output_language": output_language, "snapshot": snapshot}
                ),
            }
        ],
        max_tokens=MAX_TOKENS,
        schema=CONTACT_EXTRACT_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_contact_extract(result.output, snapshot)
        if output is not None:
            await write_ai_request(
                db,
                user_id=user.id,
                contact_id=contact_id,
                feature=CONTACT_EXTRACT_FEATURE,
                request_id=result.request_id,
                outcome=OK,
            )
            return AiAssistResult(
                OK, request_id=result.request_id, truncated=snapshot["truncated"], **output
            )
        outcome = ERROR
    else:
        outcome = result.outcome
    await write_ai_request(
        db,
        user_id=user.id,
        contact_id=contact_id,
        feature=CONTACT_EXTRACT_FEATURE,
        request_id=result.request_id,
        outcome=outcome,
    )
    return AiAssistResult(outcome, request_id=result.request_id, truncated=snapshot["truncated"])
