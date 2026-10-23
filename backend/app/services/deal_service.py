import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.user import User
from app.utils.demo_scope import deal_not_demo

VALID_STATUSES = {"lead", "following", "negotiating", "won", "lost"}
VALID_PRIORITIES = {"high", "mid", "low"}


async def create_deal(
    db: AsyncSession,
    contact_id: str,
    data: dict,
    creator_id: str,
) -> dict:
    status = data.get("status", "lead")
    contact = await _get_contact(db, contact_id)
    deal = Deal(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        title=data.get("title"),
        status=status,
        priority=data.get("priority", "mid"),
        amount=Decimal(str(data.get("amount", "0.00"))),
        source=data.get("source"),
        assigned_to=data.get("assigned_to") or (contact.assigned_to if contact else None),
        won_at=datetime.utcnow() if status == "won" else None,
    )
    db.add(deal)
    await db.flush()
    return await _deal_to_dict(db, deal)


async def list_deals(
    db: AsyncSession,
    current_user: User,
    contact_id: Optional[str] = None,
) -> list[dict]:
    scope = await _scope_conditions(db, current_user)
    query = select(Deal).where(Deal.deleted_at.is_(None), deal_not_demo(), *scope)
    if contact_id:
        query = query.where(Deal.contact_id == contact_id)
    query = query.order_by(Deal.created_at.desc())
    result = await db.execute(query)
    deals = result.scalars().all()
    return [await _deal_to_dict(db, d) for d in deals]


async def get_deal(db: AsyncSession, deal_id: str) -> Optional[dict]:
    result = await db.execute(select(Deal).where(Deal.id == deal_id, Deal.deleted_at.is_(None)))
    deal = result.scalar_one_or_none()
    if not deal:
        return None
    return await _deal_to_dict(db, deal)


async def update_deal(
    db: AsyncSession,
    deal_id: str,
    data: dict,
    updater_id: str,
) -> Optional[dict]:
    result = await db.execute(select(Deal).where(Deal.id == deal_id, Deal.deleted_at.is_(None)))
    deal = result.scalar_one_or_none()
    if not deal:
        return None

    old_status = deal.status
    new_status = data.get("status")

    for key, value in data.items():
        if value is not None and hasattr(deal, key):
            setattr(deal, key, value)
    deal.updated_at = datetime.utcnow()

    # Maintain won_at
    if new_status and new_status != old_status:
        if new_status == "won":
            deal.won_at = datetime.utcnow()
        elif old_status == "won":
            deal.won_at = None

        # Log status change as Activity
        activity = Activity(
            id=str(uuid.uuid4()),
            contact_id=deal.contact_id,
            deal_id=deal.id,
            user_id=updater_id,
            type="status change",
            content=f"Deal status changed from {old_status} to {new_status}",
            follow_date=datetime.utcnow(),
        )
        db.add(activity)

    await db.flush()
    return await _deal_to_dict(db, deal)


async def delete_deal(db: AsyncSession, deal_id: str) -> bool:
    result = await db.execute(select(Deal).where(Deal.id == deal_id, Deal.deleted_at.is_(None)))
    deal = result.scalar_one_or_none()
    if not deal:
        return False
    deal.deleted_at = datetime.utcnow()
    await db.flush()
    return True


async def cascade_delete_by_contact(db: AsyncSession, contact_id: str) -> None:
    result = await db.execute(
        select(Deal).where(Deal.contact_id == contact_id, Deal.deleted_at.is_(None))
    )
    now = datetime.utcnow()
    for deal in result.scalars().all():
        deal.deleted_at = now


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _scope_conditions(db: AsyncSession, current_user: User) -> list:
    if current_user.role == "sales":
        return [Deal.assigned_to == current_user.id]
    if current_user.role == "manager":
        r = await db.execute(
            select(User.id).where(
                (User.manager_id == current_user.id) | (User.id == current_user.id)
            )
        )
        team_ids = [row[0] for row in r.all()]
        return [Deal.assigned_to.in_(team_ids)]
    return []


async def _get_contact(db: AsyncSession, contact_id: str) -> Optional[Contact]:
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def _deal_to_dict(db: AsyncSession, deal: Deal) -> dict:
    contact_name = None
    contact_company = None
    contact = await _get_contact(db, deal.contact_id)
    if contact:
        contact_name = contact.name
        contact_company = contact.company

    assigned_to_name = None
    if deal.assigned_to:
        u = await db.execute(select(User.name).where(User.id == deal.assigned_to))
        assigned_to_name = u.scalar()

    return {
        "id": deal.id,
        "contact_id": deal.contact_id,
        "contact_name": contact_name,
        "contact_company": contact_company,
        "title": deal.title,
        "status": deal.status,
        "priority": deal.priority,
        "amount": float(deal.amount),
        "source": deal.source,
        "assigned_to": deal.assigned_to,
        "assigned_to_name": assigned_to_name,
        "won_at": deal.won_at.isoformat() if deal.won_at else None,
        "created_at": deal.created_at.isoformat() if deal.created_at else None,
        "updated_at": deal.updated_at.isoformat() if deal.updated_at else None,
    }
