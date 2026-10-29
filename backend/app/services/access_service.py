"""Object-level access: may this user touch this one record?

List endpoints narrow by role inside their own queries; this module is the same
rule applied to a single record fetched by id. Each record is judged by the
owner column its list filters on, so whatever a list shows can be opened and
nothing it hides can be.

Admins are unrestricted; a manager reaches whatever sits with someone on their
team (themselves and their direct reports); a sales rep reaches only their own.
A record with no owner therefore reaches only admins -- `None` is neither the
rep's own id nor a member of any team list -- and a record that does not exist
is refused the same way, so a caller can answer both with one response and
never confirm that an id exists.
"""

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.deal import Deal
from app.models.task import Task
from app.models.user import User
from app.services.dashboard_service import _get_team_ids


async def may_access_owner(db: AsyncSession, user: User, owner_id: Optional[str]) -> bool:
    if user.role == "admin":
        return True
    if owner_id is None:
        return False
    if user.role == "manager":
        return owner_id in await _get_team_ids(db, user.id)
    return owner_id == user.id


async def owner_scope(db: AsyncSession, user: User, owner_column: Any) -> list[Any]:
    """Return SQL predicates limiting an owner column to ``user``'s reach."""
    if user.role == "admin":
        return []
    if user.role == "manager":
        return [owner_column.in_(await _get_team_ids(db, user.id))]
    return [owner_column == user.id]


async def may_access_contact(db: AsyncSession, user: User, contact_id: str) -> bool:
    if user.role == "admin":
        return True
    owner = (
        await db.execute(
            select(Contact.assigned_to).where(
                Contact.id == contact_id, Contact.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    return await may_access_owner(db, user, owner)


async def may_access_deal(db: AsyncSession, user: User, deal_id: str) -> bool:
    """Judged by the deal's own owner, as the deal list and pipeline are -- which
    can differ from its customer's, since reassigning a customer leaves the
    deals (and the sales credit) where they were."""
    if user.role == "admin":
        return True
    owner = (
        await db.execute(
            select(Deal.assigned_to).where(Deal.id == deal_id, Deal.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    return await may_access_owner(db, user, owner)


async def may_access_task(db: AsyncSession, user: User, task_id: str) -> bool:
    if user.role == "admin":
        return True
    owner = (
        await db.execute(select(Task.assigned_to).where(Task.id == task_id))
    ).scalar_one_or_none()
    return await may_access_owner(db, user, owner)


async def assignable_users(db: AsyncSession, user: User) -> list[User]:
    """The active users ``user`` may pick as an owner, ordered by name.

    This is the assignee picker's list (``GET /api/users``): an admin gets every
    active user, a manager themselves and their direct reports, anyone else
    only themselves -- and nobody inactive, themselves included.
    """
    query = select(User).where(User.is_active == True)
    if user.role == "admin":
        pass
    elif user.role == "manager":
        query = query.where((User.manager_id == user.id) | (User.id == user.id))
    else:
        query = query.where(User.id == user.id)

    result = await db.execute(query.order_by(User.name))
    return list(result.scalars().all())


async def may_assign_to(db: AsyncSession, user: User, target_id: str) -> bool:
    """Whether `user` may hand a record to `target_id`.

    Same reach as reading: a manager may assign within their team, a rep only
    to themselves. The UI already offers no more than that -- reps get no
    assignee picker, and /api/users lists only a manager's own team -- so this
    holds the API to what the screens already promise.
    """
    return await may_access_owner(db, user, target_id)
