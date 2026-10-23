from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.user import User
from app.utils.demo_scope import contact_not_demo
from app.utils.response import ok

router = APIRouter()

STAGES = ["lead", "following", "negotiating", "won", "lost"]


@router.get("")
async def get_pipeline(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Scope by role via Deal.assigned_to
    base_where = [Deal.deleted_at.is_(None)]

    if current_user.role == "sales":
        base_where.append(Deal.assigned_to == current_user.id)
    elif current_user.role == "manager":
        team_q = select(User.id).where(User.manager_id == current_user.id)
        team_result = await db.execute(team_q)
        team_ids = [r for r in team_result.scalars().all()]
        team_ids.append(current_user.id)
        base_where.append(Deal.assigned_to.in_(team_ids))

    # Exclude deals whose contact is archived, deleted, or a gateway demo visitor
    base_where.append(
        Deal.contact_id.in_(
            select(Contact.id).where(
                Contact.deleted_at.is_(None),
                Contact.is_archived == 0,
                contact_not_demo(),
            )
        )
    )

    stages = []
    for status in STAGES:
        # Count + sum
        r = await db.execute(
            select(
                func.count(Deal.id),
                func.coalesce(func.sum(Deal.amount), 0),
            ).where(*base_where, Deal.status == status)
        )
        count, total_value = r.one()

        # Deals with contact info and assigned user name
        r = await db.execute(
            select(Deal, Contact.name, Contact.company, User.name.label("assigned_to_name"))
            .join(Contact, Contact.id == Deal.contact_id)
            .outerjoin(User, User.id == Deal.assigned_to)
            .where(*base_where, Deal.status == status)
            .order_by(Deal.amount.desc())
            .limit(100)
        )
        deals = [
            {
                "id": row.Deal.id,
                "contact_id": row.Deal.contact_id,
                "contact_name": row.name,
                "contact_company": row.company,
                "title": row.Deal.title,
                "amount": float(row.Deal.amount),
                "source": row.Deal.source,
                "priority": row.Deal.priority,
                "status": row.Deal.status,
                "assigned_to": row.Deal.assigned_to,
                "assigned_to_name": row.assigned_to_name,
                "updated_at": row.Deal.updated_at.isoformat() if row.Deal.updated_at else None,
            }
            for row in r.all()
        ]

        stages.append(
            {
                "status": status,
                "count": count,
                "total_value": float(total_value),
                "deals": deals,
            }
        )

    return ok(data={"stages": stages})
