"""Business rules for sending a CRM agent reply through ai_chatbot_demo.

The API layer supplies an already-authorized user and already-validated text.
This module intentionally owns neither authorization nor request schemas.
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import chatbot_event_service
from app.utils.phone import normalize_phone

CHATBOT_PARTY = "ai_chatbot_demo"
WINDOW = timedelta(hours=24)
TOTAL_TIMEOUT_SECONDS = 30.0

NOT_FOUND = "NOT_FOUND"
NO_PHONE = "NO_PHONE"
WINDOW_CLOSED = "WINDOW_CLOSED"
NOT_CONFIGURED = "NOT_CONFIGURED"
SEND_FAILED = "SEND_FAILED"
UNCONFIRMED = "UNCONFIRMED"


def _result(kind: str, code: str, message: Message | None = None, **extra: Any) -> dict:
    return {"result": kind, "code": code, "message": message, **extra}


def _configured() -> bool:
    return bool(settings.chatbot_send_url and settings.chatbot_send_secret)


def _body_and_headers(payload: dict) -> tuple[bytes, dict[str, str]]:
    """Encode once: this exact body, not a reconstructed JSON value, is signed."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signed = timestamp.encode("ascii") + b"." + body
    digest = hmac.new(
        settings.chatbot_send_secret.encode("utf-8"), signed, hashlib.sha256
    ).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Acuven-Timestamp": timestamp,
        "X-Acuven-Signature": f"sha256={digest}",
    }


def _response_datetime(value: Any) -> datetime | None:
    """Parse the chatbot's RFC 3339 response timestamps without guessing."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _last_inbound(db: AsyncSession, contact_id: str) -> Message | None:
    result = await db.execute(
        select(Message)
        .where(
            Message.contact_id == contact_id,
            Message.channel == "whatsapp",
            Message.direction == "inbound",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _post_send(payload: dict) -> tuple[str, dict | None]:
    """Return sent, failed, or unconfirmed and the JSON response when usable."""
    body, headers = _body_and_headers(payload)
    try:
        async with httpx.AsyncClient() as client:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                response = await client.post(
                    f"{settings.chatbot_send_url.rstrip('/')}/internal/crm/send",
                    content=body,
                    headers=headers,
                    timeout=httpx.Timeout(TOTAL_TIMEOUT_SECONDS),
                )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return "failed", None
    except (asyncio.TimeoutError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError):
        return "unconfirmed", None
    except httpx.HTTPError:
        return "unconfirmed", None

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        data = None

    if response.status_code == 200 and isinstance(data, dict):
        if data.get("result") in {"sent", "duplicate"} and data.get("wa_message_id"):
            return "sent", data
    if response.status_code in {401, 422, 503}:
        return "failed", data if isinstance(data, dict) else None
    if response.status_code == 502 and isinstance(data, dict) and data.get("result") == "rejected":
        return "failed", data
    return "unconfirmed", data if isinstance(data, dict) else None


async def send_chatbot_reply(
    db: AsyncSession,
    *,
    contact_id: str,
    text: str,
    agent_id: str,
    agent_name: str,
) -> dict:
    """Store then send an agent reply, preserving ambiguity after transport errors."""
    contact = (
        await db.execute(
            select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if contact is None:
        return _result("not_found", NOT_FOUND)

    phone = normalize_phone(contact.phone)
    if phone is None:
        return _result("no_phone", NO_PHONE)

    last_inbound = await _last_inbound(db, contact.id)
    closes_at = last_inbound.created_at + WINDOW if last_inbound else None
    if closes_at is None or datetime.utcnow() >= closes_at:
        return _result("window_closed", WINDOW_CLOSED, closes_at=closes_at)

    if not _configured():
        return _result("not_configured", NOT_CONFIGURED)

    message = Message(
        id=str(uuid.uuid4()),
        contact_id=contact.id,
        channel="whatsapp",
        direction="outbound",
        sender_id=CHATBOT_PARTY,
        recipient_id=phone,
        body=text,
        external_id=None,
        is_read=True,
        assigned_to=contact.assigned_to,
        sent_by=agent_id,
        author_kind="agent",
        delivery_status=None,
    )
    db.add(message)
    await db.commit()

    outcome, response = await _post_send(
        {
            "request_id": message.id,
            "visitor": {"phone": phone, "bsuid": contact.whatsapp_bsuid},
            "text": text,
            "agent": {"id": agent_id, "name": agent_name},
        }
    )
    if outcome == "failed":
        error = {
            key: response[key]
            for key in ("error_code", "error_title")
            if response is not None and response.get(key) is not None
        }
        await db.delete(message)
        await db.commit()
        return _result("failed", SEND_FAILED, error=error or None)

    if outcome == "unconfirmed":
        return _result("unconfirmed", UNCONFIRMED, message)

    await db.execute(
        update(Message)
        .where(Message.id == message.id, Message.external_id.is_(None))
        .values(external_id=response["wa_message_id"])
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(Message)
        .where(Message.id == message.id, Message.delivery_status.is_(None))
        .values(delivery_status="sent")
        .execution_options(synchronize_session=False)
    )
    handover = response.get("handover")
    occurred_at = (
        _response_datetime(handover.get("occurred_at")) if isinstance(handover, dict) else None
    )
    if occurred_at is not None:
        await chatbot_event_service.write_response_handover(
            db,
            contact_id=contact.id,
            active=bool(handover.get("active")),
            expires_at=_response_datetime(handover.get("expires_at")),
            occurred_at=occurred_at,
        )
    await db.commit()
    await db.refresh(message)
    return _result("sent", "SENT", message)
