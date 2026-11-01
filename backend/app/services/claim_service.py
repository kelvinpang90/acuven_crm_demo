"""Conversation claims: who is replying to a contact right now (docs/chatbot-phase3.md 5.2).

Business rules only. Whether the user may access the contact is the API
layer's check; nothing here looks at ownership. Changes are written with
conditional updates and not committed; the caller's session commits them,
so a send can commit its claim together with the message it stores.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.services import chatbot_event_service

CLAIM_DURATION = timedelta(minutes=15)

OK = "ok"
CLAIMED = "claimed"
NOT_FOUND = "not_found"


@dataclass
class ClaimResult:
    """``claimed_by`` / ``claimed_until`` are the claim after the call on
    success, and the other user's claim when the category is CLAIMED."""

    category: str
    claimed_by: Optional[str] = None
    claimed_until: Optional[datetime] = None
    taken_over_from: Optional[str] = None


def active_claim(contact: Contact, now: datetime) -> Optional[tuple[str, datetime]]:
    """The valid claim as (user id, until), or None. A claim expiring exactly
    at ``now`` is no longer valid."""
    until = contact.reply_claimed_until
    if contact.reply_claimed_by is None or until is None or not now < until:
        return None
    return contact.reply_claimed_by, until


def _current(now: Optional[datetime]) -> datetime:
    return chatbot_event_service.to_naive_utc(now) if now else datetime.utcnow()


def _claimable(user_id: str, now: datetime):
    """Nobody holds the claim, it is the user's own, or it has expired."""
    return or_(
        Contact.reply_claimed_by.is_(None),
        Contact.reply_claimed_by == user_id,
        Contact.reply_claimed_until <= now,
    )


async def _live_contact(db: AsyncSession, contact_id: str) -> Optional[Contact]:
    result = await db.execute(
        select(Contact)
        .where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _write(db: AsyncSession, contact_id: str, condition, **values) -> bool:
    statement = update(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    if condition is not None:
        statement = statement.where(condition)
    result = await db.execute(statement.values(**values))
    return result.rowcount == 1


async def _held_by_other(db: AsyncSession, contact_id: str) -> ClaimResult:
    """Result for a conditional write that matched no row."""
    contact = await _live_contact(db, contact_id)
    if contact is None:
        return ClaimResult(NOT_FOUND)
    return ClaimResult(
        CLAIMED, claimed_by=contact.reply_claimed_by, claimed_until=contact.reply_claimed_until
    )


async def try_claim(db: AsyncSession, *, contact_id: str, user_id: str, now: datetime) -> bool:
    """Take or extend the claim in one conditional update; False if another
    user's valid claim (or a deleted contact) stopped it."""
    return await _write(
        db,
        contact_id,
        _claimable(user_id, now),
        reply_claimed_by=user_id,
        reply_claimed_until=now + CLAIM_DURATION,
    )


async def claim(
    db: AsyncSession,
    *,
    contact_id: str,
    user_id: str,
    takeover: bool = False,
    now: Optional[datetime] = None,
) -> ClaimResult:
    current = _current(now)
    contact = await _live_contact(db, contact_id)
    if contact is None:
        return ClaimResult(NOT_FOUND)
    until = current + CLAIM_DURATION

    if not takeover:
        if not await try_claim(db, contact_id=contact_id, user_id=user_id, now=current):
            return await _held_by_other(db, contact_id)
        return ClaimResult(OK, claimed_by=user_id, claimed_until=until)

    held = active_claim(contact, current)
    taken_over_from = held[0] if held is not None and held[0] != user_id else None
    if not await _write(db, contact_id, None, reply_claimed_by=user_id, reply_claimed_until=until):
        return ClaimResult(NOT_FOUND)
    return ClaimResult(OK, claimed_by=user_id, claimed_until=until, taken_over_from=taken_over_from)


async def release(
    db: AsyncSession,
    *,
    contact_id: str,
    user_id: str,
    takeover: bool = False,
    now: Optional[datetime] = None,
) -> ClaimResult:
    """Clear the claim. Another user's valid claim is only cleared with
    ``takeover`` (design section 14 items 4 and 8)."""
    current = _current(now)
    if await _live_contact(db, contact_id) is None:
        return ClaimResult(NOT_FOUND)
    condition = None if takeover else _claimable(user_id, current)
    if not await _write(db, contact_id, condition, reply_claimed_by=None, reply_claimed_until=None):
        return await _held_by_other(db, contact_id)
    return ClaimResult(OK)
