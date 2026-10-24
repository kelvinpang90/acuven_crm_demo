"""Bounded chat snapshots for extracting contact details (docs/contact-ai-extract.md)."""

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.message import Message
from app.services.ai_api_client import compact_json

# Same ten values, in the same order, as INDUSTRIES in frontend ContactForm.tsx.
INDUSTRIES = (
    "科技/IT",
    "金融/保险",
    "医疗/健康",
    "教育/培训",
    "零售/电商",
    "制造/工业",
    "房地产",
    "咨询/服务",
    "餐饮/酒店",
    "物流/供应链",
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
