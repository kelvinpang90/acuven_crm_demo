"""Handling of ai_chatbot_demo conversation events (docs/chatbot-events.md
sections 5 and 9.1, 9.4).

Business rules only: the entry point takes plain, already validated values,
never the API layer's request models, and makes no outbound requests. Changes
are flushed, not committed; the caller's session commits them.

Each handler returns one of the results the endpoint answers with:
`stored`, `duplicate` or `ignored`.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.services import routing_service
from app.utils.phone import normalize_phone

STORED = "stored"
DUPLICATE = "duplicate"
IGNORED = "ignored"

logger = logging.getLogger(__name__)

MESSAGE_EVENTS = ("customer_message", "ai_reply", "console_reply")
_AUTHOR_KIND = {"customer_message": "customer", "ai_reply": "ai", "console_reply": "console"}

# The chatbot side of a message's sender / recipient fields.
CHATBOT_PARTY = "ai_chatbot_demo"

CONTACT_NAME_MAX = 100
DELIVERY_ERROR_MAX = 300

# Delivery statuses only move up this order. `failed` sits outside it: it may
# replace no status, `sent` or `delivered`, and nothing replaces it (9.4).
_DELIVERY_RANK = {None: 0, "sent": 1, "delivered": 2, "read": 3}
_FAILED_REPLACES = (None, "sent", "delivered")


def to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Naive UTC, the shape every datetime column here stores. A naive input
    is taken to be UTC already."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def process_event(
    db: AsyncSession,
    *,
    event_type: str,
    occurred_at: datetime,
    phone: Optional[str] = None,
    bsuid: Optional[str] = None,
    profile_name: Optional[str] = None,
    wa_message_id: Optional[str] = None,
    body: Optional[str] = None,
    bot_id: Optional[str] = None,
    handover_active: Optional[bool] = None,
    handover_expires_at: Optional[datetime] = None,
    status: Optional[str] = None,
    error_code: Optional[str] = None,
    error_title: Optional[str] = None,
) -> str:
    """Apply one event. Fields a type doesn't use are ignored."""
    if event_type in MESSAGE_EVENTS:
        return await store_message(
            db,
            event_type=event_type,
            occurred_at=occurred_at,
            phone=phone,
            bsuid=bsuid,
            profile_name=profile_name,
            wa_message_id=wa_message_id,
            body=body,
            bot_id=bot_id,
        )
    if event_type == "handover_changed":
        return await update_handover(
            db,
            occurred_at=occurred_at,
            phone=phone,
            bsuid=bsuid,
            active=bool(handover_active),
            expires_at=handover_expires_at,
        )
    if event_type == "delivery_status":
        return await update_delivery_status(
            db,
            wa_message_id=wa_message_id,
            status=status,
            error_code=error_code,
            error_title=error_title,
        )
    raise ValueError(f"unknown chatbot event type: {event_type!r}")


# ---------------------------------------------------------------------------
# Finding the contact
# ---------------------------------------------------------------------------


async def _contact_by_phone(db: AsyncSession, phone: str) -> Optional[Contact]:
    """Earliest created live contact with this number (P2); ties go to the
    smallest id."""
    result = await db.execute(
        select(Contact)
        .where(Contact.phone == phone, Contact.deleted_at.is_(None))
        .order_by(Contact.created_at.asc(), Contact.id.asc())
        .limit(1)
    )
    return result.scalars().first()


async def _contact_by_bsuid(db: AsyncSession, bsuid: str) -> Optional[Contact]:
    result = await db.execute(
        select(Contact)
        .where(Contact.whatsapp_bsuid == bsuid, Contact.deleted_at.is_(None))
        .limit(1)
    )
    return result.scalars().first()


async def _find_contact(
    db: AsyncSession, phone: Optional[str], bsuid: Optional[str]
) -> Optional[Contact]:
    """Section 5 steps 1 and 2 without changing anything. `phone` is already
    normalized (or None)."""
    contact = await _contact_by_phone(db, phone) if phone else None
    if contact is None and bsuid:
        contact = await _contact_by_bsuid(db, bsuid)
    return contact


async def _release_bsuid_from_deleted(db: AsyncSession, bsuid: str) -> None:
    """The unique index on whatsapp_bsuid also covers soft-deleted contacts;
    take the BSUID off them before a live contact uses it (9.1)."""
    await db.execute(
        update(Contact)
        .where(Contact.whatsapp_bsuid == bsuid, Contact.deleted_at.is_not(None))
        .values(whatsapp_bsuid=None)
        .execution_options(synchronize_session="fetch")
    )


async def _find_or_create_contact(
    db: AsyncSession,
    phone: Optional[str],
    bsuid: Optional[str],
    profile_name: Optional[str],
) -> Contact:
    """Section 5 "找联系人" for message events. `phone` is already normalized
    (or None); at least one of `phone` and `bsuid` is set."""
    contact = await _contact_by_phone(db, phone) if phone else None
    if contact is not None:
        # Fill in the BSUID unless another live contact already holds it.
        if bsuid and not contact.whatsapp_bsuid and await _contact_by_bsuid(db, bsuid) is None:
            await _release_bsuid_from_deleted(db, bsuid)
            contact.whatsapp_bsuid = bsuid
            await db.flush()
        return contact

    if bsuid:
        contact = await _contact_by_bsuid(db, bsuid)
        if contact is not None:
            # The visitor has shared their number since.
            if phone and not contact.phone:
                contact.phone = phone
                await db.flush()
            return contact
        await _release_bsuid_from_deleted(db, bsuid)

    contact = Contact(
        id=str(uuid.uuid4()),
        name=(profile_name or phone or bsuid)[:CONTACT_NAME_MAX],
        phone=phone,
        whatsapp_bsuid=bsuid,
        is_gateway=False,
    )
    db.add(contact)
    await db.flush()
    assigned = await routing_service.assign_contact(db, contact)
    if assigned:
        contact.assigned_to = assigned
    db.add(
        Deal(
            id=str(uuid.uuid4()),
            contact_id=contact.id,
            status="lead",
            priority="mid",
            amount=0.0,
            assigned_to=contact.assigned_to,
        )
    )
    await db.flush()
    return contact


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def store_message(
    db: AsyncSession,
    *,
    event_type: str,
    occurred_at: datetime,
    phone: Optional[str],
    bsuid: Optional[str],
    profile_name: Optional[str],
    wa_message_id: str,
    body: str,
    bot_id: Optional[str] = None,
) -> str:
    """customer_message / ai_reply / console_reply."""
    if event_type not in MESSAGE_EVENTS:
        raise ValueError(f"not a message event: {event_type!r}")

    existing = await db.execute(select(Message.id).where(Message.external_id == wa_message_id))
    if existing.scalar_one_or_none() is not None:
        return DUPLICATE

    # A number that cannot be normalized counts as no number.
    phone = normalize_phone(phone)
    bsuid = bsuid or None
    if phone is None and bsuid is None:
        return IGNORED

    contact = await _find_or_create_contact(db, phone, bsuid, profile_name)
    visitor = phone or bsuid

    inbound = event_type == "customer_message"
    db.add(
        Message(
            id=str(uuid.uuid4()),
            contact_id=contact.id,
            channel="whatsapp",
            direction="inbound" if inbound else "outbound",
            sender_id=visitor if inbound else CHATBOT_PARTY,
            recipient_id=CHATBOT_PARTY if inbound else visitor,
            body=body,
            external_id=wa_message_id,
            is_read=not inbound,
            assigned_to=contact.assigned_to,
            author_kind=_AUTHOR_KIND[event_type],
            bot_id=bot_id if event_type == "ai_reply" else None,
            created_at=to_naive_utc(occurred_at),
        )
    )
    await db.flush()
    return STORED


async def store_agent_reply(
    db: AsyncSession,
    *,
    crm_message_id: str,
    wa_message_id: str,
    body: str,
) -> str:
    """Confirm an already persisted CRM agent reply from a validated event."""
    del body  # The CRM-created body remains the source of truth.
    result = await db.execute(select(Message).where(Message.id == crm_message_id))
    message = result.scalar_one_or_none()
    if message is None or message.author_kind != "agent":
        return IGNORED
    if message.external_id == wa_message_id:
        return DUPLICATE
    if message.external_id is not None:
        logger.warning(
            "agent_reply external id conflicts with CRM message",
            extra={"crm_message_id": crm_message_id, "wa_message_id": wa_message_id},
        )
        return IGNORED

    existing = await db.execute(
        select(Message.id).where(Message.external_id == wa_message_id, Message.id != message.id)
    )
    if existing.scalar_one_or_none() is not None:
        return DUPLICATE
    message.external_id = wa_message_id
    if message.delivery_status is None:
        message.delivery_status = "sent"
    await db.flush()
    return STORED


async def update_handover(
    db: AsyncSession,
    *,
    occurred_at: datetime,
    phone: Optional[str],
    bsuid: Optional[str],
    active: bool,
    expires_at: Optional[datetime],
) -> str:
    """handover_changed: only finds the contact, never creates or fills it in."""
    contact = await _find_contact(db, normalize_phone(phone), bsuid or None)
    if contact is None:
        return IGNORED

    occurred_at = to_naive_utc(occurred_at)
    # A replayed older event must not overwrite a newer state.
    last = contact.wa_handover_updated_at
    if last is not None and occurred_at < last:
        return IGNORED

    contact.wa_handover_active = active
    contact.wa_handover_expires_at = to_naive_utc(expires_at) if active else None
    contact.wa_handover_updated_at = occurred_at
    await db.flush()
    return STORED


async def write_response_handover(
    db: AsyncSession,
    *,
    contact_id: str,
    occurred_at: datetime,
    active: bool,
    expires_at: Optional[datetime],
) -> str:
    """Write a synchronous chatbot response's handover state.

    Response writes do not share the event queue's arrival ordering. They only
    win when their timestamp is strictly newer; ``update_handover`` deliberately
    retains its existing equal-timestamp behaviour for queued events.
    """
    occurred_at = to_naive_utc(occurred_at)
    result = await db.execute(
        update(Contact)
        .where(
            Contact.id == contact_id,
            or_(
                Contact.wa_handover_updated_at.is_(None),
                Contact.wa_handover_updated_at < occurred_at,
            ),
        )
        .values(
            wa_handover_active=active,
            wa_handover_expires_at=to_naive_utc(expires_at) if active else None,
            wa_handover_updated_at=occurred_at,
        )
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        return IGNORED
    await db.flush()
    return STORED


def _status_may_replace(current: Optional[str], new: str) -> bool:
    if new != "failed" and (new is None or new not in _DELIVERY_RANK):
        raise ValueError(f"unknown delivery status: {new!r}")
    if current == "failed":
        return False
    if new == "failed":
        return current in _FAILED_REPLACES
    return _DELIVERY_RANK[new] > _DELIVERY_RANK[current]


def _delivery_error(error_code, error_title) -> Optional[str]:
    parts = [str(p) for p in (error_code, error_title) if p is not None and str(p) != ""]
    return " ".join(parts)[:DELIVERY_ERROR_MAX] or None


async def update_delivery_status(
    db: AsyncSession,
    *,
    wa_message_id: str,
    status: str,
    error_code=None,
    error_title: Optional[str] = None,
) -> str:
    """delivery_status: statuses only move up; `failed` is final."""
    result = await db.execute(select(Message).where(Message.external_id == wa_message_id))
    message = result.scalar_one_or_none()
    if message is None:
        return IGNORED

    if not _status_may_replace(message.delivery_status, status):
        return IGNORED

    message.delivery_status = status
    if status == "failed":
        message.delivery_error = _delivery_error(error_code, error_title)
    await db.flush()
    return STORED
