"""Inbox conversation intent and urgency labels (docs/inbox-ai-triage.md)."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.contact import Contact
from app.models.conversation_triage import ConversationTriage
from app.models.message import Message
from app.services import ai_api_client
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    NOT_CONFIGURED,
    OK,
    SKIPPED,
    SUPERSEDED,
    AiAssistResult,
    write_ai_request,
)
from app.services.ai_features import (
    MAX_TOKENS,
    MESSAGE_TRIAGE_FEATURE,
    MESSAGE_TRIAGE_SCHEMA,
    MESSAGE_TRIAGE_SYSTEM,
    validate_message_triage,
)

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 10
# Lets the rest of a customer's burst arrive first (design section 9 item 4).
TRIAGE_DELAY_SECONDS = 20
# Messages dated later than this are ignored when judging "newer" (section 9 items 5, 10).
FUTURE_TOLERANCE = timedelta(minutes=5)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None, microsecond=0).isoformat() + "Z"


def _message_item(message: Message) -> dict[str, Any]:
    return {
        "at": _utc(message.created_at),
        "direction": message.direction,
        "author_kind": message.author_kind
        or ("customer" if message.direction == "inbound" else "agent"),
        "channel": message.channel,
        "body": (message.body or "")[:500],
    }


async def build_triage_snapshot(
    db: AsyncSession, *, contact: Contact, now: datetime
) -> dict[str, Any]:
    """Build the model-facing snapshot for one already-loaded, undeleted contact."""
    # Messages are not filtered by now, the same as the other snapshots.
    del now
    records = list(
        (
            await db.execute(
                select(Message)
                .where(Message.contact_id == contact.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(MESSAGE_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    records.reverse()
    return {
        "contact": {
            "name": (contact.name or "")[:100],
            "company": contact.company[:200] if contact.company is not None else None,
        },
        "messages": [_message_item(record) for record in records],
    }


async def _has_newer_inbound(db: AsyncSession, message: Message, now: datetime) -> bool:
    newer = (
        await db.execute(
            select(Message.id)
            .where(
                Message.contact_id == message.contact_id,
                Message.direction == "inbound",
                Message.created_at <= now + FUTURE_TOLERANCE,
                or_(
                    Message.created_at > message.created_at,
                    and_(Message.created_at == message.created_at, Message.id > message.id),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return newer is not None


def _is_newer(candidate: Message, message: Message, now: datetime) -> bool:
    if candidate.created_at > now + FUTURE_TOLERANCE:
        return False
    return (candidate.created_at, candidate.id) > (message.created_at, message.id)


async def _covers_newer_message(
    db: AsyncSession, row: ConversationTriage | None, message: Message, now: datetime
) -> bool:
    if row is None or row.message_id == message.id:
        return False
    covered = await db.get(Message, row.message_id)
    return covered is not None and _is_newer(covered, message, now)


async def classify_conversation(
    db: AsyncSession, *, contact_id: str, message_id: str
) -> AiAssistResult:
    """Label one conversation; the caller owns the session and commits."""
    contact = (
        await db.execute(
            select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if contact is None:
        return AiAssistResult(SKIPPED)
    message = await db.get(Message, message_id)
    if message is None or message.contact_id != contact_id or message.direction != "inbound":
        return AiAssistResult(SKIPPED)
    now = _utc_now()
    if await _has_newer_inbound(db, message, now):
        return AiAssistResult(SUPERSEDED)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)

    snapshot = await build_triage_snapshot(db, contact=contact, now=now)
    result = await ai_api_client.send_message(
        feature=MESSAGE_TRIAGE_FEATURE,
        system=MESSAGE_TRIAGE_SYSTEM,
        messages=[
            {"role": "user", "content": compact_json({"as_of": _utc(now), "snapshot": snapshot})}
        ],
        max_tokens=MAX_TOKENS,
        schema=MESSAGE_TRIAGE_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_message_triage(result.output)
        outcome = ERROR if output is None else OK
    else:
        output = None
        outcome = result.outcome
    await write_ai_request(
        db,
        user_id=None,
        contact_id=contact_id,
        feature=MESSAGE_TRIAGE_FEATURE,
        request_id=result.request_id,
        outcome=outcome,
    )
    if output is None:
        return AiAssistResult(outcome, request_id=result.request_id)

    # The AI call takes a while; a newer message may have arrived or been labelled meanwhile
    # (design section 9 item 6).
    now = _utc_now()
    row = (
        await db.execute(
            select(ConversationTriage)
            .where(ConversationTriage.contact_id == contact_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if await _has_newer_inbound(db, message, now) or await _covers_newer_message(
        db, row, message, now
    ):
        return AiAssistResult(SUPERSEDED, request_id=result.request_id, **output)
    if row is None:
        row = ConversationTriage(contact_id=contact_id)
        db.add(row)
    row.intent = output["intent"]
    row.urgency = output["urgency"]
    row.message_id = message_id
    row.request_id = result.request_id
    row.triaged_at = now
    await db.flush()
    return AiAssistResult(OK, request_id=result.request_id, **output)


async def run_triage(contact_id: str, message_id: str, delay: float = TRIAGE_DELAY_SECONDS) -> None:
    """Background-task wrapper: own session and commit; errors are only logged."""
    if not settings.ai_triage_enabled or not ai_api_client.is_ai_configured():
        return
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        async with AsyncSessionLocal() as db:
            result = await classify_conversation(db, contact_id=contact_id, message_id=message_id)
            await db.commit()
        logger.info(
            "Message triage contact_id=%s message_id=%s category=%s request_id=%s",
            contact_id,
            message_id,
            result.category,
            result.request_id,
        )
    except Exception:
        logger.exception(
            "Message triage failed contact_id=%s message_id=%s", contact_id, message_id
        )
