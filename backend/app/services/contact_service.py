import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.user import User
from app.services import access_service, routing_service
from app.utils.demo_scope import contact_not_demo

VALID_STATUSES = {"lead", "following", "negotiating", "won", "lost"}
VALID_PRIORITIES = {"high", "mid", "low"}


async def list_contacts(
    db: AsyncSession,
    current_user: User,
    search: Optional[str] = None,
    industry: Optional[str] = None,
    assigned_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "created_at",
    order: str = "desc",
    is_archived: int = 0,
    unassigned: int = 0,
) -> dict:
    query = select(Contact).where(
        Contact.deleted_at.is_(None),
        Contact.is_archived == is_archived,
        contact_not_demo(),
    )

    # Data permission
    if current_user.role == "sales":
        query = query.where(Contact.assigned_to == current_user.id)
    elif current_user.role == "manager":
        team_ids_q = select(User.id).where(User.manager_id == current_user.id)
        team_result = await db.execute(team_ids_q)
        team_ids = [r for r in team_result.scalars().all()]
        team_ids.append(current_user.id)
        query = query.where(Contact.assigned_to.in_(team_ids))

    # Filters
    if search:
        query = query.where(
            or_(
                Contact.name.ilike(f"%{search}%"),
                Contact.company.ilike(f"%{search}%"),
            )
        )
    if industry:
        query = query.where(Contact.industry == industry)
    if assigned_to:
        query = query.where(Contact.assigned_to == assigned_to)
    # Stacks on the data permission above: sales and managers only ever reach
    # owned customers, so for them this narrows to nothing rather than widening.
    if unassigned:
        query = query.where(Contact.assigned_to.is_(None))

    # Count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Sort
    deal_amount_subq = (
        select(func.coalesce(func.sum(Deal.amount), 0))
        .where(Deal.contact_id == Contact.id, Deal.deleted_at.is_(None))
        .correlate(Contact)
        .scalar_subquery()
    )
    if sort_by == "deal_value":
        sort_expr = deal_amount_subq.desc() if order == "desc" else deal_amount_subq.asc()
        query = query.order_by(sort_expr)
    else:
        sort_col = getattr(Contact, sort_by, Contact.created_at)
        query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    # Paginate
    page_size = min(page_size, 100)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    contacts = result.scalars().all()

    # Bulk fetch deal summaries
    contact_ids = [c.id for c in contacts]
    deal_summary = await _bulk_deal_summary(db, contact_ids)

    # Enrich with assigned_to_name
    data = []
    user_cache: dict[str, str] = {}
    for c in contacts:
        d = _contact_to_dict(c)
        if c.assigned_to:
            if c.assigned_to not in user_cache:
                u = await db.execute(select(User.name).where(User.id == c.assigned_to))
                user_cache[c.assigned_to] = u.scalar() or ""
            d["assigned_to_name"] = user_cache[c.assigned_to]
        d["total_deal_amount"] = deal_summary.get(c.id, {}).get("total", 0.0)
        d["deal_count"] = deal_summary.get(c.id, {}).get("count", 0)
        data.append(d)

    return {"data": data, "total": total, "page": page, "page_size": page_size}


async def get_contact(db: AsyncSession, contact_id: str) -> Optional[dict]:
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = result.scalar_one_or_none()
    if not contact:
        return None

    d = _contact_to_dict(contact)
    if contact.assigned_to:
        u = await db.execute(select(User.name).where(User.id == contact.assigned_to))
        d["assigned_to_name"] = u.scalar() or ""
    summary = await _bulk_deal_summary(db, [contact_id])
    d["total_deal_amount"] = summary.get(contact_id, {}).get("total", 0.0)
    d["deal_count"] = summary.get(contact_id, {}).get("count", 0)
    return d


async def create_contact(db: AsyncSession, data: dict, current_user: User) -> dict:
    if data.get("assigned_to"):
        await _validate_assigned_user(db, data["assigned_to"])
    elif current_user.role == "sales":
        data["assigned_to"] = current_user.id

    contact = Contact(
        id=str(uuid.uuid4()),
        name=data["name"],
        company=data.get("company"),
        industry=data.get("industry"),
        email=data.get("email"),
        phone=data.get("phone"),
        address=data.get("address"),
        notes=data.get("notes"),
        assigned_to=data.get("assigned_to"),
        tags=data.get("tags"),
        autocount_customer_code=data.get("autocount_customer_code"),
    )
    db.add(contact)
    await db.flush()

    # Auto-assign via routing if no explicit assignee
    if not contact.assigned_to:
        assigned = await routing_service.assign_contact(db, contact)
        if assigned:
            contact.assigned_to = assigned
            await db.flush()

    # Auto-create initial Deal
    from app.models.deal import Deal as DealModel

    deal = DealModel(
        id=str(uuid.uuid4()),
        contact_id=contact.id,
        title=data.get("initial_title"),
        status=data.get("initial_status", "lead"),
        priority=data.get("initial_priority", "mid"),
        amount=data.get("initial_amount", 0.0),
        assigned_to=contact.assigned_to,
        won_at=datetime.utcnow() if data.get("initial_status") == "won" else None,
    )
    db.add(deal)
    await db.flush()

    return _contact_to_dict(contact)


async def update_contact(
    db: AsyncSession, contact_id: str, data: dict, current_user: User
) -> Optional[dict]:
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = result.scalar_one_or_none()
    if not contact:
        return None

    if "assigned_to" in data and not data["assigned_to"]:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="assigned_to cannot be empty when updating")
    if "assigned_to" in data and data["assigned_to"]:
        await _validate_assigned_user(db, data["assigned_to"])

    new_assignee = data.get("assigned_to")
    reassigned = bool(new_assignee) and new_assignee != contact.assigned_to

    allowed_fields = {
        "name",
        "company",
        "industry",
        "email",
        "phone",
        "address",
        "notes",
        "assigned_to",
        "tags",
        "last_contact",
        "is_archived",
        "autocount_customer_code",
    }
    for key, value in data.items():
        if key in allowed_fields and value is not None and hasattr(contact, key):
            setattr(contact, key, value)
    contact.updated_at = datetime.utcnow()

    if reassigned:
        # Message.assigned_to is a copy of the owner taken when the message was
        # stored, and the inbox filters on it. Without this the new owner would
        # inherit the customer but see none of the conversation, while the
        # previous owner would still see all of it.
        await db.execute(
            update(Message).where(Message.contact_id == contact.id).values(assigned_to=new_assignee)
        )

    await db.flush()
    return _contact_to_dict(contact)


async def archive_contact(db: AsyncSession, contact_id: str, is_archived: int) -> Optional[dict]:
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = result.scalar_one_or_none()
    if not contact:
        return None
    contact.is_archived = is_archived
    contact.updated_at = datetime.utcnow()
    await db.flush()
    return _contact_to_dict(contact)


async def soft_delete_contact(db: AsyncSession, contact_id: str) -> bool:
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = result.scalar_one_or_none()
    if not contact:
        return False
    now = datetime.utcnow()
    contact.deleted_at = now

    # Cascade soft-delete all deals for this contact
    from app.services.deal_service import cascade_delete_by_contact

    await cascade_delete_by_contact(db, contact_id)

    await db.flush()
    return True


async def import_contacts(
    db: AsyncSession,
    rows: list[dict],
    current_user: User,
    auto_assign: bool = False,
    assign_strategy: str = "rules",
) -> dict:
    """Import contacts from parsed Excel rows.

    Row with customer_id: only add a new Deal to the existing Contact.
    Row without customer_id: create new Contact + initial Deal.
    """
    from app.models.deal import Deal as DealModel
    from app.models.user import User as UserModel

    inserted = 0
    deals_added = 0
    skipped = 0
    errors = []

    # Cache active users by email
    active_users: dict[str, str] = {}
    user_result = await db.execute(
        select(UserModel.id, UserModel.email).where(UserModel.is_active == True)
    )
    for uid, uemail in user_result.all():
        active_users[uemail] = uid

    for i, row in enumerate(rows, start=2):
        row_errors = []

        assigned_email = (row.get("assigned_to_email") or "").strip()
        assigned_user_id = None
        if assigned_email:
            assigned_user_id = active_users.get(assigned_email)
            # Someone the importer may not assign to reads the same as a missing
            # account, so the sheet cannot probe which emails exist.
            if assigned_user_id and not await access_service.may_assign_to(
                db, current_user, assigned_user_id
            ):
                assigned_user_id = None
            if not assigned_user_id:
                row_errors.append(
                    {
                        "row": i,
                        "field": "assigned_to_email",
                        "message": "Sales account not found or inactive",
                    }
                )

        status = (row.get("status") or "").strip() or "lead"
        if status not in VALID_STATUSES:
            row_errors.append({"row": i, "field": "status", "message": f"Invalid status: {status}"})
            status = "lead"

        priority = (row.get("priority") or "").strip() or "mid"
        if priority not in VALID_PRIORITIES:
            priority = "mid"

        customer_id = (row.get("id") or "").strip()

        if customer_id:
            # Mode: add Deal to existing Contact
            existing = await db.execute(
                select(Contact).where(Contact.id == customer_id, Contact.deleted_at.is_(None))
            )
            contact = existing.scalar_one_or_none()
            # Someone else's customer reads the same as a missing one.
            if not contact or not await access_service.may_access_contact(
                db, current_user, customer_id
            ):
                errors.append(
                    {
                        "row": i,
                        "field": "customer_id",
                        "message": f"Contact not found: {customer_id}",
                    }
                )
                skipped += 1
                continue

            if row_errors:
                errors.extend(row_errors)
                skipped += 1
                continue

            try:
                amount = float(row.get("deal_value") or 0)
            except (ValueError, TypeError):
                amount = 0.0

            deal = DealModel(
                id=str(uuid.uuid4()),
                contact_id=customer_id,
                title=(row.get("deal_title") or "").strip() or None,
                status=status,
                priority=priority,
                amount=amount,
                assigned_to=assigned_user_id or contact.assigned_to,
                won_at=datetime.utcnow() if status == "won" else None,
            )
            db.add(deal)
            deals_added += 1

        else:
            # Mode: create new Contact + initial Deal
            name = (row.get("name") or "").strip()
            if not name:
                row_errors.append({"row": i, "field": "name", "message": "Name is required"})

            email = (row.get("email") or "").strip()
            if email and "@" not in email:
                row_errors.append({"row": i, "field": "email", "message": "Invalid email format"})

            if row_errors:
                errors.extend(row_errors)
                skipped += 1
                continue

            try:
                amount = float(row.get("deal_value") or 0)
            except (ValueError, TypeError):
                amount = 0.0

            tags_str = (row.get("tags") or "").strip()
            tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else None

            final_assigned = assigned_user_id or (
                current_user.id if current_user.role == "sales" else None
            )

            contact = Contact(
                id=str(uuid.uuid4()),
                name=name,
                company=row.get("company") or None,
                industry=row.get("industry") or None,
                email=email or None,
                phone=row.get("phone") or None,
                address=row.get("address") or None,
                notes=row.get("notes") or None,
                tags=tags,
                assigned_to=final_assigned,
            )
            # Rows carrying no owner of their own: without this, an import run by
            # an admin leaves every contact ownerless, and an ownerless contact is
            # invisible to sales and managers in both the list and the inbox.
            # Resolved before the flush so the deal below inherits the same owner.
            if auto_assign and not final_assigned:
                if assign_strategy == "me":
                    final_assigned = current_user.id
                else:
                    final_assigned = await routing_service.assign_by_strategy(
                        db, contact, assign_strategy
                    )
                contact.assigned_to = final_assigned

            db.add(contact)
            await db.flush()

            deal = DealModel(
                id=str(uuid.uuid4()),
                contact_id=contact.id,
                title=(row.get("deal_title") or "").strip() or None,
                status=status,
                priority=priority,
                amount=amount,
                assigned_to=final_assigned,
                won_at=datetime.utcnow() if status == "won" else None,
            )
            db.add(deal)
            inserted += 1

    await db.flush()
    return {
        "total": len(rows),
        "inserted": inserted,
        "deals_added": deals_added,
        "skipped": skipped,
        "errors": errors,
    }


async def _validate_assigned_user(db: AsyncSession, user_id: str) -> None:
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Assigned sales user not found or inactive")


async def _bulk_deal_summary(db: AsyncSession, contact_ids: list[str]) -> dict:
    if not contact_ids:
        return {}
    r = await db.execute(
        select(
            Deal.contact_id,
            func.count(Deal.id).label("cnt"),
            func.coalesce(func.sum(Deal.amount), 0).label("total"),
        )
        .where(Deal.contact_id.in_(contact_ids), Deal.deleted_at.is_(None))
        .group_by(Deal.contact_id)
    )
    return {row.contact_id: {"count": row.cnt, "total": float(row.total)} for row in r.all()}


def _contact_to_dict(contact: Contact) -> dict:
    return {
        "id": contact.id,
        "name": contact.name,
        "company": contact.company,
        "industry": contact.industry,
        "email": contact.email,
        "phone": contact.phone,
        "address": contact.address,
        "notes": contact.notes,
        "assigned_to": contact.assigned_to,
        "assigned_to_name": None,
        "last_contact": contact.last_contact.isoformat() if contact.last_contact else None,
        "tags": contact.tags,
        "is_archived": contact.is_archived,
        "autocount_customer_code": contact.autocount_customer_code,
        "total_deal_amount": 0.0,
        "deal_count": 0,
        "created_at": contact.created_at.isoformat() if contact.created_at else None,
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
    }
