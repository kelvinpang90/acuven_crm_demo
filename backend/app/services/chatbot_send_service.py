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
HANDBACK_FAILED = "chatbot_failed"
HANDBACK_ENDED = "ended"
HANDBACK_NOT_ACTIVE = "not_active"

RETRY_IN_PROGRESS = timedelta(seconds=45)

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
    """The current handover state after a handback attempt."""

    category: str
    active: Optional[bool] = None
    expires_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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


def _current(now: Optional[datetime]) -> datetime:
    return chatbot_event_service.to_naive_utc(now) if now else datetime.utcnow()


def _request_headers(raw_body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Acuven-Timestamp": timestamp,
        "X-Acuven-Signature": _request_signature(timestamp, raw_body),
    }


async def _post_chatbot(
    path: str, payload: dict[str, Any]
) -> tuple[Optional[httpx.Response], dict[str, Any], bool]:
    """Post a signed CRM request and classify only definite transport failures."""
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    response = None
    definite_failure = False
    try:
        async with asyncio.timeout(30):
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    f"{settings.chatbot_send_url.rstrip('/')}{path}",
                    content=raw_body,
                    headers=_request_headers(raw_body),
                )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        definite_failure = True
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
    return response, response_payload, definite_failure


def _send_failure(
    response: Optional[httpx.Response], payload: dict[str, Any], transport_failed: bool
) -> bool:
    return transport_failed or (
        response is not None
        and (
            response.status_code in (401, 422, 503)
            or (response.status_code == 502 and payload.get("result") == "rejected")
        )
    )


async def _confirm_sent(
    db: AsyncSession,
    *,
    message_id: str,
    contact_id: str,
    wa_message_id: str,
    payload: dict[str, Any],
) -> Message:
    """Apply the conditional confirmation writes shared by send and retry."""
    await db.execute(
        update(Message)
        .where(Message.id == message_id, Message.external_id.is_(None))
        .values(external_id=wa_message_id)
    )
    await db.execute(
        update(Message)
        .where(Message.id == message_id, Message.delivery_status.is_(None))
        .values(delivery_status="sent")
    )
    handover = _handover(payload)
    if handover is not None:
        await chatbot_event_service.write_response_handover(db, contact_id=contact_id, **handover)
    await db.commit()
    return await _message(db, message_id)


def _sent_response(response: Optional[httpx.Response], payload: dict[str, Any]) -> Optional[str]:
    wa_message_id = payload.get("wa_message_id")
    if (
        response is not None
        and response.status_code == 200
        and payload.get("result") in ("sent", "duplicate")
        and isinstance(wa_message_id, str)
        and wa_message_id
    ):
        return wa_message_id
    return None


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
    current = _current(now)
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
    response, payload_response, transport_failed = await _post_chatbot(
        "/internal/crm/send", payload
    )
    wa_message_id = _sent_response(response, payload_response)
    if wa_message_id:
        return SendReplyResult(
            SENT,
            message=await _confirm_sent(
                db,
                message_id=message.id,
                contact_id=contact.id,
                wa_message_id=wa_message_id,
                payload=payload_response,
            ),
        )

    error_code = payload_response.get("error_code")
    title = payload_response.get("error_title")
    error_title = title if isinstance(title, str) else None
    if _send_failure(response, payload_response, transport_failed):
        await db.delete(message)
        await db.commit()
        return SendReplyResult(SEND_FAILED, error_code=error_code, error_title=error_title)

    # Anything not positively classified above is deliberately unconfirmed.
    return SendReplyResult(UNCONFIRMED, message=await _message(db, message.id))


async def retry_agent_reply(
    db: AsyncSession, *, message_id: str, now: Optional[datetime] = None
) -> SendReplyResult:
    """Retry one unconfirmed agent message without ever deleting it."""
    result = await db.execute(select(Message).where(Message.id == message_id))
    message = result.scalar_one_or_none()
    if message is None or message.author_kind != "agent":
        return SendReplyResult(NOT_RETRYABLE)
    if message.external_id:
        return SendReplyResult(SENT, message=message)

    current = _current(now)
    if current < message.created_at + RETRY_IN_PROGRESS:
        return SendReplyResult(IN_PROGRESS)
    if current >= message.created_at + REPLY_WINDOW:
        return SendReplyResult(RETRY_EXPIRED)

    contact_result = await db.execute(
        select(Contact).where(Contact.id == message.contact_id, Contact.deleted_at.is_(None))
    )
    contact = contact_result.scalar_one_or_none()
    if contact is None:
        return SendReplyResult(NOT_FOUND)

    latest_inbound = await _last_inbound(db, contact.id)
    window_until = latest_inbound + REPLY_WINDOW if latest_inbound else None
    if window_until is None or current >= window_until:
        return SendReplyResult(WINDOW_CLOSED, window_until=window_until)
    if not (settings.chatbot_send_url and settings.chatbot_send_secret):
        return SendReplyResult(NOT_CONFIGURED)

    sender = None
    if message.sent_by:
        sender_result = await db.execute(select(User.name).where(User.id == message.sent_by))
        sender = sender_result.scalar_one_or_none()
    agent_id = message.sent_by or ""
    payload = {
        "request_id": message.id,
        "visitor": {"phone": message.recipient_id, "bsuid": contact.whatsapp_bsuid},
        "text": message.body,
        "agent": {"id": agent_id, "name": sender or agent_id},
    }
    response, response_payload, transport_failed = await _post_chatbot(
        "/internal/crm/send", payload
    )
    wa_message_id = _sent_response(response, response_payload)
    if wa_message_id:
        return SendReplyResult(
            SENT,
            message=await _confirm_sent(
                db,
                message_id=message.id,
                contact_id=contact.id,
                wa_message_id=wa_message_id,
                payload=response_payload,
            ),
        )
    title = response_payload.get("error_title")
    return SendReplyResult(
        SEND_FAILED if _send_failure(response, response_payload, transport_failed) else UNCONFIRMED,
        error_code=response_payload.get("error_code"),
        error_title=title if isinstance(title, str) else None,
    )


def _handover_result(category: str, contact: Optional[Contact] = None) -> HandbackResult:
    if contact is None:
        return HandbackResult(category)
    return HandbackResult(
        category,
        active=contact.wa_handover_active,
        expires_at=contact.wa_handover_expires_at,
        updated_at=contact.wa_handover_updated_at,
    )


async def handback_to_ai(
    db: AsyncSession,
    *,
    contact_id: str,
    agent_id: str,
    agent_name: str,
) -> HandbackResult:
    """Ask chatbot to end a contact's human handover without creating activity."""
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return _handover_result(NOT_FOUND)
    phone = normalize_phone(contact.phone)
    bsuid = contact.whatsapp_bsuid or None
    if phone is None and bsuid is None:
        return _handover_result(NO_IDENTITY, contact)
    if not (settings.chatbot_send_url and settings.chatbot_send_secret):
        return _handover_result(NOT_CONFIGURED, contact)

    payload = {
        "request_id": str(uuid.uuid4()),
        "visitor": {"phone": phone, "bsuid": bsuid},
        "agent": {"id": agent_id, "name": agent_name},
    }
    response, response_payload, _ = await _post_chatbot("/internal/crm/handback", payload)
    if response is None or response.status_code != 200:
        return _handover_result(HANDBACK_FAILED, contact)
    response_result = response_payload.get("result")
    if response_result == "not_active":
        return _handover_result(HANDBACK_NOT_ACTIVE, contact)
    if response_result != "ended":
        logger.error(
            "chatbot handback returned an invalid result",
            extra={"contact_id": contact.id, "result": response_result},
        )
        return _handover_result(HANDBACK_FAILED, contact)
    handover = _handover(response_payload)
    if handover is None:
        logger.error(
            "chatbot handback ended without a valid handover", extra={"contact_id": contact.id}
        )
        return _handover_result(HANDBACK_FAILED, contact)
    await chatbot_event_service.write_response_handover(db, contact_id=contact.id, **handover)
    await db.commit()
    refreshed = await _message_contact(db, contact.id)
    return _handover_result(HANDBACK_ENDED, refreshed)


async def _message_contact(db: AsyncSession, contact_id: str) -> Contact:
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()
