import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.deal import Deal
from app.models.routing_rule import RoutingRule
from app.models.user import User
from app.services.access_service import assignable_users

# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def list_rules(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(RoutingRule).order_by(RoutingRule.priority.asc()))
    return [_rule_to_dict(r) for r in result.scalars().all()]


async def create_rule(db: AsyncSession, data: dict, created_by: str) -> dict:
    rule = RoutingRule(
        id=str(uuid.uuid4()),
        name=data["name"],
        strategy=data["strategy"],
        conditions=data.get("conditions"),
        target_users=data.get("target_users", []),
        priority=data.get("priority", 0),
        is_active=data.get("is_active", True),
        created_by=created_by,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _rule_to_dict(rule)


async def update_rule(db: AsyncSession, rule_id: str, data: dict) -> Optional[dict]:
    result = await db.execute(select(RoutingRule).where(RoutingRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return None

    for key in ("name", "strategy", "conditions", "target_users", "priority", "is_active"):
        if key in data and data[key] is not None:
            setattr(rule, key, data[key])

    await db.commit()
    await db.refresh(rule)
    return _rule_to_dict(rule)


async def delete_rule(db: AsyncSession, rule_id: str) -> bool:
    result = await db.execute(select(RoutingRule).where(RoutingRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return False
    await db.delete(rule)
    await db.commit()
    return True


async def toggle_rule(db: AsyncSession, rule_id: str) -> Optional[dict]:
    result = await db.execute(select(RoutingRule).where(RoutingRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return None
    rule.is_active = not rule.is_active
    await db.commit()
    await db.refresh(rule)
    return _rule_to_dict(rule)


async def reorder_rules(db: AsyncSession, items: list[dict]) -> list[dict]:
    for item in items:
        result = await db.execute(select(RoutingRule).where(RoutingRule.id == item["id"]))
        rule = result.scalar_one_or_none()
        if rule:
            rule.priority = item["priority"]
    await db.commit()
    return await list_rules(db)


# ---------------------------------------------------------------------------
# Auto-assignment
# ---------------------------------------------------------------------------


async def assign_contact(db: AsyncSession, contact: Contact) -> Optional[str]:
    """Determine the best sales rep to assign a contact to.
    Returns user_id or None."""

    user_id, _rule = await _pick_by_rules(db, contact)
    if user_id:
        return user_id
    return await _fallback_sales_id(db)


async def _pick_by_rules(
    db: AsyncSession, contact: Contact
) -> tuple[Optional[str], Optional[RoutingRule]]:
    """Try the active rules in priority order.

    Returns the chosen user_id and the rule that chose them, or (None, None)
    when no rule picks anybody.
    """
    rules = await db.execute(
        select(RoutingRule)
        .where(RoutingRule.is_active == True)
        .order_by(RoutingRule.priority.asc())
    )

    for rule in rules.scalars().all():
        target_ids = rule.target_users or []
        if not target_ids:
            continue

        # Filter to active users only
        active_result = await db.execute(
            select(User.id).where(
                User.id.in_(target_ids),
                User.is_active == True,
                User.role == "sales",
            )
        )
        eligible = [r for r in active_result.scalars().all()]
        if not eligible:
            continue

        if rule.strategy == "workload":
            user_id = await _strategy_workload(db, eligible)
        elif rule.strategy == "region":
            user_id = await _strategy_region(db, contact, rule.conditions, eligible)
        elif rule.strategy == "win_rate":
            user_id = await _strategy_win_rate(db, eligible)
        else:
            continue

        if user_id:
            return user_id, rule

    return None, None


async def _fallback_sales_id(db: AsyncSession) -> Optional[str]:
    """Fallback when no rule picks anybody: the earliest-created active sales."""
    fallback = await db.execute(
        select(User.id)
        .where(User.is_active == True, User.role == "sales")
        .order_by(User.created_at.asc())
        .limit(1)
    )
    return fallback.scalar_one_or_none()


# Strategies a caller may name explicitly, e.g. the bulk importer. "rules" runs
# the configured routing rules, which is what every other entry point does; the
# rest bypass the rules and apply one strategy to every contact. "region" is
# absent on purpose — it only means anything alongside a rule's keyword
# conditions, so it is reachable through "rules" and not on its own.
ASSIGN_STRATEGIES = ("rules", "workload", "win_rate")


async def _active_sales_ids(db: AsyncSession) -> list[str]:
    result = await db.execute(select(User.id).where(User.is_active == True, User.role == "sales"))
    return [r for r in result.scalars().all()]


async def assign_by_strategy(db: AsyncSession, contact: Contact, strategy: str) -> Optional[str]:
    """Pick an owner using a strategy the caller names.

    Unlike `assign_contact()`, which follows the configured rules and their
    target lists, this considers every active sales rep. Returns None for an
    unknown strategy or when there is nobody to assign to.
    """
    if strategy == "rules":
        return await assign_contact(db, contact)

    eligible = await _active_sales_ids(db)
    if not eligible:
        return None

    if strategy == "workload":
        return await _strategy_workload(db, eligible)
    if strategy == "win_rate":
        return await _strategy_win_rate(db, eligible)
    return None


async def _strategy_workload(db: AsyncSession, eligible: list[str]) -> Optional[str]:
    """Assign to sales with fewest active contacts."""
    result = await db.execute(
        select(
            User.id,
            func.count(Contact.id).label("cnt"),
        )
        .outerjoin(
            Contact,
            (Contact.assigned_to == User.id) & Contact.deleted_at.is_(None),
        )
        .where(User.id.in_(eligible))
        .group_by(User.id)
        .order_by(func.count(Contact.id).asc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def _strategy_region(
    db: AsyncSession,
    contact: Contact,
    conditions: Optional[dict],
    eligible: list[str],
) -> Optional[str]:
    """Match contact address/company to region keywords."""
    if not conditions:
        return None

    keywords = conditions.get("keywords", [])
    if not keywords:
        return None

    # Check if contact's address or company matches any keyword
    text_to_match = " ".join(filter(None, [contact.address, contact.company, contact.name]))

    for kw in keywords:
        if kw in text_to_match:
            # Region matches — use workload among eligible
            return await _strategy_workload(db, eligible)

    return None


async def _strategy_win_rate(db: AsyncSession, eligible: list[str]) -> Optional[str]:
    """Assign to sales with highest win rate in last 90 days."""
    cutoff = datetime.utcnow() - timedelta(days=90)

    result = await db.execute(
        select(
            Deal.assigned_to,
            func.count(case((Deal.status == "won", 1))).label("won"),
            func.count(Deal.id).label("total"),
        )
        .where(
            Deal.deleted_at.is_(None),
            Deal.assigned_to.in_(eligible),
            Deal.created_at >= cutoff,
        )
        .group_by(Deal.assigned_to)
    )
    rows = result.all()

    best_id = None
    best_rate = -1.0
    for uid, won, total in rows:
        rate = won / total if total > 0 else 0
        if rate > best_rate:
            best_rate = rate
            best_id = uid

    # Users with no deals yet — give them a chance
    if best_id is None and eligible:
        return eligible[0]

    return best_id


# ---------------------------------------------------------------------------
# Assignee suggestion (docs/contact-assign-suggest.md)
# ---------------------------------------------------------------------------


async def suggest_assignee(
    db: AsyncSession,
    current_user: User,
    *,
    name: Optional[str],
    company: Optional[str],
    industry: Optional[str],
    address: Optional[str],
) -> dict:
    """Dry-run the routing rules on form fields and list the pickable owners.

    Read-only: the probe contact never joins the session and nothing is
    flushed, committed or changed.
    """
    name, company, industry, address = (_clean(v) for v in (name, company, industry, address))

    candidates = await _candidate_stats(db, current_user, industry)

    probe = Contact(name=name, company=company, industry=industry, address=address)
    user_id, rule = await _pick_by_rules(db, probe)
    fallback = not user_id
    if fallback:
        user_id = await _fallback_sales_id(db)

    user_name = None
    if user_id:
        user_name = (
            await db.execute(select(User.name).where(User.id == user_id))
        ).scalar_one_or_none()

    return {
        "rule_pick": {
            "user_id": user_id,
            "user_name": user_name,
            "rule_name": None if fallback else rule.name,
            "fallback": fallback,
            "selectable": user_id is not None and any(c["user_id"] == user_id for c in candidates),
        },
        "candidates": candidates,
    }


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip() or None


async def _candidate_stats(
    db: AsyncSession, current_user: User, industry: Optional[str]
) -> list[dict]:
    """The assignee picker's people with their workload and results.

    One grouped query per figure; people with no rows get 0.
    """
    users = await assignable_users(db, current_user)
    ids = [u.id for u in users]

    contact_counts: dict = {}
    open_deal_counts: dict = {}
    recent: dict = {}
    industry_contacts: dict = {}
    industry_won: dict = {}

    if ids:
        # Same basis as _strategy_workload: archived contacts count.
        rows = await db.execute(
            select(Contact.assigned_to, func.count(Contact.id))
            .where(Contact.assigned_to.in_(ids), Contact.deleted_at.is_(None))
            .group_by(Contact.assigned_to)
        )
        contact_counts = dict(rows.all())

        rows = await db.execute(
            select(Deal.assigned_to, func.count(Deal.id))
            .where(
                Deal.assigned_to.in_(ids),
                Deal.deleted_at.is_(None),
                Deal.status.notin_(("won", "lost")),
            )
            .group_by(Deal.assigned_to)
        )
        open_deal_counts = dict(rows.all())

        # Same basis as _strategy_win_rate.
        cutoff = datetime.utcnow() - timedelta(days=90)
        rows = await db.execute(
            select(
                Deal.assigned_to,
                func.count(case((Deal.status == "won", 1))),
                func.count(Deal.id),
            )
            .where(
                Deal.assigned_to.in_(ids),
                Deal.deleted_at.is_(None),
                Deal.created_at >= cutoff,
            )
            .group_by(Deal.assigned_to)
        )
        recent = {uid: (won, total) for uid, won, total in rows.all()}

        if industry is not None:
            rows = await db.execute(
                select(Contact.assigned_to, func.count(Contact.id))
                .where(
                    Contact.assigned_to.in_(ids),
                    Contact.deleted_at.is_(None),
                    Contact.industry == industry,
                )
                .group_by(Contact.assigned_to)
            )
            industry_contacts = dict(rows.all())

            rows = await db.execute(
                select(Deal.assigned_to, func.count(Deal.id))
                .join(Contact, Contact.id == Deal.contact_id)
                .where(
                    Deal.assigned_to.in_(ids),
                    Deal.deleted_at.is_(None),
                    Deal.status == "won",
                    Contact.deleted_at.is_(None),
                    Contact.industry == industry,
                )
                .group_by(Deal.assigned_to)
            )
            industry_won = dict(rows.all())

    candidates = []
    for u in users:
        won_90d, total_90d = recent.get(u.id, (0, 0))
        candidates.append(
            {
                "user_id": u.id,
                "name": u.name,
                "contact_count": contact_counts.get(u.id, 0),
                "open_deal_count": open_deal_counts.get(u.id, 0),
                "won_90d": won_90d,
                "total_90d": total_90d,
                "industry_contact_count": (
                    None if industry is None else industry_contacts.get(u.id, 0)
                ),
                "industry_won_count": None if industry is None else industry_won.get(u.id, 0),
            }
        )

    candidates.sort(key=lambda c: (c["contact_count"], c["name"]))
    return candidates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule_to_dict(r: RoutingRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "is_active": r.is_active,
        "priority": r.priority,
        "strategy": r.strategy,
        "conditions": r.conditions,
        "target_users": r.target_users,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
