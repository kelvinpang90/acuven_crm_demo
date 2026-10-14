"""CRM business rules for sending an agent reply through ai_chatbot_demo.

This module intentionally has no HTTP or request-model concerns from the CRM
API. The API layer validates ownership and message text before calling it.
"""

import asyncio
import hashlib
import hmac
import json
import logging
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
from app.models.user import User
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
NOT_RETRYABLE = "not_retryable"
IN_PROGRESS = "in_progress"
RETRY_EXPIRED = "retry_expired"
NO_IDENTITY = "no_identity"
CHATBOT_FAILED = "chatbot_failed"
HANDOFF_FAILED = CHATBOT_FAILED
ENDED = "ended"
NOT_ACTIVE = "not_active"

logger = logging.getLogger(__name__)


@dataclass
class SendReplyResult:
    """The API layer maps ``category`` to its existing response contract."""

    category: str
    message: Optional[Message] = None
    window_until: Optional[datetime] = None
    error_code: Optional[Any] = None
    error_title: Optional[str] = None


@dataclass
class HandbackResult:
    category: str
    contact: Optional[Contact] = None


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


async def _live_contact(db: AsyncSession, contact_id: str) -> Optional[Contact]:
    result = await db.execute(
        select(Contact)
        .where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _post_chatbot(path: str, payload: dict[str, Any]):
    """Make one signed chatbot request and preserve unknown outcomes."""
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Acuven-Timestamp": timestamp,
        "X-Acuven-Signature": _request_signature(timestamp, raw_body),
    }
    response = None
    connection_failure = False
    try:
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    f"{settings.chatbot_send_url.rstrip('/')}{path}",
                    content=raw_body,
                    headers=headers,
                )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        connection_failure = True
    except (asyncio.TimeoutError, httpx.HTTPError):
        pass
    except Exception:
        pass

    response_payload: dict[str, Any] = {}
    if response is not None:
        try:
            parsed = response.json()
            response_payload = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            pass
    return response, response_payload, connection_failure


async def _apply_sent_response(
    db: AsyncSession, message: Message, contact: Contact, payload: dict[str, Any]
) -> SendReplyResult:
    wa_message_id = payload["wa_message_id"]
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
    handover = _handover(payload)
    if handover is not None:
        await chatbot_event_service.write_response_handover(db, contact_id=contact.id, **handover)
    await db.commit()
    return SendReplyResult(SENT, message=await _message(db, message.id))


def _explicit_failure(response, payload: dict[str, Any], connection_failure: bool) -> bool:
    """Design section 6 rule 8: only these outcomes prove chatbot did not send."""
    return connection_failure or (
        response is not None
        and (
            response.status_code in (401, 422, 503)
            or (response.status_code == 502 and payload.get("result") == "rejected")
        )
    )


def _sent(response, payload: dict[str, Any]) -> bool:
    return (
        response is not None
        and response.status_code == 200
        and payload.get("result") in ("sent", "duplicate")
        and isinstance(payload.get("wa_message_id"), str)
        and payload["wa_message_id"] != ""
    )


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
    contact = await _live_contact(db, contact_id)
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
    response, payload_response, connection_failure = await _post_chatbot(
        "/internal/crm/send", payload
    )
    error_code = payload_response.get("error_code")
    title = payload_response.get("error_title")
    error_title = title if isinstance(title, str) else None
    explicit_failure = _explicit_failure(response, payload_response, connection_failure)
    if _sent(response, payload_response):
        return await _apply_sent_response(db, message, contact, payload_response)

    if explicit_failure:
        await db.delete(message)
        await db.commit()
        return SendReplyResult(SEND_FAILED, error_code=error_code, error_title=error_title)

    # Anything not positively classified above is deliberately unconfirmed.
    return SendReplyResult(UNCONFIRMED, message=await _message(db, message.id))


async def retry_agent_reply(
    db: AsyncSession, *, message_id: str, now: Optional[datetime] = None
) -> SendReplyResult:
    """Retry one unconfirmed agent reply without ever deleting its durable row."""
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if message is None or message.author_kind != "agent":
        return SendReplyResult(NOT_RETRYABLE)
    if message.external_id is not None:
        return SendReplyResult(SENT, message=message)

    current = chatbot_event_service.to_naive_utc(now) if now else datetime.utcnow()
    if current < message.created_at + timedelta(seconds=45):
        return SendReplyResult(IN_PROGRESS, message=message)
    if current >= message.created_at + REPLY_WINDOW:
        return SendReplyResult(RETRY_EXPIRED, message=message)

    contact = await _live_contact(db, message.contact_id)
    if contact is None:
        return SendReplyResult(NOT_FOUND)
    latest_inbound = await _last_inbound(db, contact.id)
    window_until = latest_inbound + REPLY_WINDOW if latest_inbound else None
    if window_until is None or current >= window_until:
        return SendReplyResult(WINDOW_CLOSED, message=message, window_until=window_until)
    if not (settings.chatbot_send_url and settings.chatbot_send_secret):
        return SendReplyResult(NOT_CONFIGURED, message=message)

    agent_name = message.sent_by
    if message.sent_by:
        user = (
            await db.execute(select(User).where(User.id == message.sent_by))
        ).scalar_one_or_none()
        if user is not None:
            agent_name = user.name
    payload = {
        "request_id": message.id,
        "visitor": {"phone": message.recipient_id, "bsuid": contact.whatsapp_bsuid},
        "text": message.body,
        "agent": {"id": message.sent_by, "name": agent_name},
    }
    # End the read-only transaction so the slow call holds no snapshot and the
    # re-read below sees rows an event may have changed in the meantime.
    await db.commit()
    response, response_payload, connection_failure = await _post_chatbot(
        "/internal/crm/send", payload
    )
    if _sent(response, response_payload):
        return await _apply_sent_response(db, message, contact, response_payload)
    # A failed retry only says this attempt did not send; the first attempt's
    # outcome is still unknown, so the row is kept whatever the category.
    category = (
        SEND_FAILED
        if _explicit_failure(response, response_payload, connection_failure)
        else UNCONFIRMED
    )
    title = response_payload.get("error_title")
    return SendReplyResult(
        category,
        message=await _message(db, message.id),
        error_code=response_payload.get("error_code"),
        error_title=title if isinstance(title, str) else None,
    )


async def handback_to_ai(
    db: AsyncSession,
    *,
    contact_id: str,
    agent_id: str,
    agent_name: str,
) -> HandbackResult:
    """Ask chatbot to end handover; only a valid ended response changes CRM."""
    contact = await _live_contact(db, contact_id)
    if contact is None:
        return HandbackResult(NOT_FOUND)
    phone = normalize_phone(contact.phone)
    if phone is None and not contact.whatsapp_bsuid:
        return HandbackResult(NO_IDENTITY, contact=contact)
    if not (settings.chatbot_send_url and settings.chatbot_send_secret):
        return HandbackResult(NOT_CONFIGURED, contact=contact)
    payload = {
        "request_id": str(uuid.uuid4()),
        "visitor": {"phone": phone, "bsuid": contact.whatsapp_bsuid},
        "agent": {"id": agent_id, "name": agent_name},
    }
    # As in retry: no open snapshot during the call, and every result re-reads
    # the contact because a handover event may arrive before the response.
    await db.commit()
    response, response_payload, _ = await _post_chatbot("/internal/crm/handback", payload)
    if response is None or response.status_code != 200:
        return HandbackResult(HANDOFF_FAILED, contact=await _live_contact(db, contact.id))
    if response_payload.get("result") == "not_active":
        return HandbackResult(NOT_ACTIVE, contact=await _live_contact(db, contact.id))
    handover = _handover(response_payload)
    if (
        response_payload.get("result") != "ended"
        or handover is None
        or handover["active"] is not False
        or handover["expires_at"] is not None
    ):
        logger.error("invalid chatbot handback response", extra={"contact_id": contact_id})
        return HandbackResult(HANDOFF_FAILED, contact=await _live_contact(db, contact.id))
    await chatbot_event_service.write_response_handover(db, contact_id=contact.id, **handover)
    await db.commit()
    return HandbackResult(ENDED, contact=await _live_contact(db, contact.id))
