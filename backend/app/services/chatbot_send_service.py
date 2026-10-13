"""CRM business rules for sending an agent reply through ai_chatbot_demo.

This module intentionally has no HTTP or request-model concerns from the CRM
API. The API layer validates ownership and message text before calling it.
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import chatbot_event_service
from app.utils.phone import normalize_phone

CHATBOT_PARTY = "ai_chatbot_demo"
REPLY_WINDOW = timedelta(hours=24)

NOT_FOUND = "not_found"
NO_PHONE = "no_phone"
WINDOW_CLOSED = "window_closed"
NOT_CONFIGURED = "not_configured"
SENT = "sent"
SEND_FAILED = "send_failed"
UNCONFIRMED = "unconfirmed"


@dataclass
class SendReplyResult:
    """The API layer maps ``category`` to its existing response contract."""

    category: str
    message: Optional[Message] = None
    window_until: Optional[datetime] = None
    error_code: Optional[Any] = None
    error_title: Optional[str] = None


def _request_signature(timestamp: str, body: bytes) -> str:
    signed = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(settings.chatbot_send_secret.encode("utf-8"), signed, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


async def _last_inbound(db: AsyncSession, contact_id: str) -> Optional[datetime]:
    result = await db.execute(
        select(Message.created_at)
        .where(
            Message.contact_id == contact_id,
            Message.channel == "whatsapp",
            Message.direction == "inbound",
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _message(db: AsyncSession, message_id: str) -> Message:
    result = await db.execute(
        select(Message).where(Message.id == message_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


def _handover(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    handover = payload.get("handover")
    if not isinstance(handover, dict):
        return None
    occurred_at = handover.get("occurred_at")
    if not isinstance(occurred_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or not isinstance(handover.get("active"), bool):
        return None
    expires_at = handover.get("expires_at")
    if expires_at is not None:
        if not isinstance(expires_at, str):
            return None
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    return {"active": handover["active"], "occurred_at": parsed, "expires_at": expires_at}


async def send_agent_reply(
    db: AsyncSession,
    *,
    contact_id: str,
    text: str,
    agent_id: str,
    agent_name: str,
    now: Optional[datetime] = None,
) -> SendReplyResult:
    """Persist first, then send one agent reply using its message id as idempotency key."""
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return SendReplyResult(NOT_FOUND)

    phone = normalize_phone(contact.phone)
    if phone is None:
        return SendReplyResult(NO_PHONE)

    latest_inbound = await _last_inbound(db, contact.id)
    window_until = latest_inbound + REPLY_WINDOW if latest_inbound else None
    current = chatbot_event_service.to_naive_utc(now) if now else datetime.utcnow()
    if window_until is None or current >= window_until:
        return SendReplyResult(WINDOW_CLOSED, window_until=window_until)

    if not (settings.chatbot_send_url and settings.chatbot_send_secret):
        return SendReplyResult(NOT_CONFIGURED)

    message = Message(
        id=str(uuid.uuid4()),
        contact_id=contact.id,
        channel="whatsapp",
        direction="outbound",
        sender_id=CHATBOT_PARTY,
        recipient_id=phone,
        body=text,
        external_id=None,
        delivery_status=None,
        is_read=True,
        assigned_to=contact.assigned_to,
        sent_by=agent_id,
        author_kind="agent",
    )
    db.add(message)
    # The callback and the synchronous response must both be able to find this
    # row, so this is intentionally a real commit before the network request.
    await db.commit()

    payload = {
        "request_id": message.id,
        "visitor": {"phone": phone, "bsuid": contact.whatsapp_bsuid},
        "text": text,
        "agent": {"id": agent_id, "name": agent_name},
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Acuven-Timestamp": timestamp,
        "X-Acuven-Signature": _request_signature(timestamp, raw_body),
    }

    response = None
    explicit_failure = False
    error_code = None
    error_title = None
    try:
        # httpx's timeout applies to individual operations; asyncio enforces
        # the contract's total thirty-second deadline too.
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    f"{settings.chatbot_send_url.rstrip('/')}/internal/crm/send",
                    content=raw_body,
                    headers=headers,
                )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        explicit_failure = True
    except (asyncio.TimeoutError, httpx.HTTPError):
        pass

    payload_response: dict[str, Any] = {}
    if response is not None:
        try:
            parsed = response.json()
            payload_response = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            payload_response = {}
        error_code = payload_response.get("error_code")
        title = payload_response.get("error_title")
        error_title = title if isinstance(title, str) else None
        explicit_failure = explicit_failure or (
            response.status_code in (401, 422, 503)
            or (response.status_code == 502 and payload_response.get("result") == "rejected")
        )

    wa_message_id = payload_response.get("wa_message_id")
    sent = (
        response is not None
        and response.status_code == 200
        and payload_response.get("result") in ("sent", "duplicate")
        and isinstance(wa_message_id, str)
        and wa_message_id != ""
    )
    if sent:
        # Do not use this object's stale values: an event may have updated the
        # row between the request and this response.
        await db.execute(
            update(Message)
            .where(Message.id == message.id, Message.external_id.is_(None))
            .values(external_id=wa_message_id)
        )
        await db.execute(
            update(Message)
            .where(Message.id == message.id, Message.delivery_status.is_(None))
            .values(delivery_status="sent")
        )
        handover = _handover(payload_response)
        if handover is not None:
            await chatbot_event_service.write_response_handover(
                db, contact_id=contact.id, **handover
            )
        await db.commit()
        return SendReplyResult(SENT, message=await _message(db, message.id))

    if explicit_failure:
        await db.delete(message)
        await db.commit()
        return SendReplyResult(SEND_FAILED, error_code=error_code, error_title=error_title)

    # Anything not positively classified above is deliberately unconfirmed.
    return SendReplyResult(UNCONFIRMED, message=await _message(db, message.id))
