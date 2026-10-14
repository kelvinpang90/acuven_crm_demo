"""Database-side building blocks shared by inbox AI assistance features."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.services.ai_api_client import compact_json

SNAPSHOT_LIMIT_BYTES = 16 * 1024


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
    db: AsyncSession, contact_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Build the bounded, model-safe conversation snapshot for a contact."""
    now = now or datetime.utcnow()
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise ValueError("contact not found")

    deals = (
        (
            await db.execute(
                select(Deal)
                .where(
                    Deal.contact_id == contact_id,
                    Deal.deleted_at.is_(None),
                    Deal.status.not_in(("won", "lost")),
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
