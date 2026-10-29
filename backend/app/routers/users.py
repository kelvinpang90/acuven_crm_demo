from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.user import User
from app.services.access_service import assignable_users
from app.utils.response import fail, ok
from app.utils.security import hash_password

router = APIRouter()


# ---------- Schemas ----------


class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str = "sales"
    manager_id: Optional[str] = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ("admin", "manager", "sales"):
            raise ValueError("Invalid role")
        return v


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    manager_id: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


# ---------- Helpers ----------


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "avatar_url": u.avatar_url,
        "language": u.language,
        "manager_id": u.manager_id,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "updated_at": u.updated_at.isoformat() if u.updated_at else None,
    }


# ---------- Endpoints ----------


@router.get("")
async def list_users(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    users = await assignable_users(db, current_user)
    return ok(data=[_user_to_dict(u) for u in users])


@router.get("/all")
async def list_all_users(
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Admin: list all users including inactive ones."""
    result = await db.execute(select(User).order_by(User.name))
    users = result.scalars().all()
    return ok(data=[_user_to_dict(u) for u in users])


@router.post("")
async def create_user(
    body: UserCreate,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Check duplicate email
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        return fail(message="Email already exists", code=400)

    user = User(
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        manager_id=body.manager_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return ok(data=_user_to_dict(user))


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.name is not None:
        user.name = body.name
    if body.email is not None:
        # Check duplicate
        dup = await db.execute(select(User).where(User.email == body.email, User.id != user_id))
        if dup.scalar_one_or_none():
            return fail(message="Email already exists", code=400)
        user.email = body.email
    if body.role is not None:
        if body.role not in ("admin", "manager", "sales"):
            return fail(message="Invalid role", code=400)
        user.role = body.role
    if body.manager_id is not None:
        user.manager_id = body.manager_id
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)

    await db.commit()
    await db.refresh(user)
    return ok(data=_user_to_dict(user))


@router.patch("/{user_id}/toggle")
async def toggle_user(
    user_id: str,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id:
        return fail(message="Cannot deactivate your own account", code=400)

    user.is_active = not user.is_active
    await db.commit()
    await db.refresh(user)
    return ok(data=_user_to_dict(user))


@router.patch("/me/language")
async def update_language(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: dict,
):
    language = body.get("language")
    if language not in ("zh", "en"):
        return fail(message="Language must be 'zh' or 'en'", code=400)

    current_user.language = language
    await db.commit()
    await db.refresh(current_user)
    return ok(data=_user_to_dict(current_user))
