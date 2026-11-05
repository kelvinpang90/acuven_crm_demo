from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.sales_target import SalesTarget
from app.models.task import Task
from app.models.user import User


async def _get_team_ids(db: AsyncSession, manager_id: str) -> list[str]:
    result = await db.execute(select(User.id).where(User.manager_id == manager_id))
    ids = [r for r in result.scalars().all()]
    ids.append(manager_id)
    return ids


def _deal_in_stats():
    """Deals that count towards business figures: not deleted."""
    return Deal.deleted_at.is_(None)


def _contact_in_stats():
    """Contacts that count towards business figures: not deleted."""
    return Contact.deleted_at.is_(None)


# ---------------------------------------------------------------------------
# Admin Dashboard
# ---------------------------------------------------------------------------


async def get_admin_dashboard(db: AsyncSession) -> dict:
    today = date.today()
    now = datetime.utcnow()
    year, month = now.year, now.month

    # KPI 1: new leads today (new contacts created today)
    r = await db.execute(
        select(func.count(Contact.id)).where(
            _contact_in_stats(),
            func.date(Contact.created_at) == today,
        )
    )
    new_leads_today = r.scalar() or 0

    # KPI 2: follow-up today (tasks due today, not done)
    r = await db.execute(
        select(func.count(Task.id)).where(
            Task.due_date == today,
            Task.is_done == False,
        )
    )
    follow_up_today = r.scalar() or 0

    # KPI 3: quoting count (deals in negotiating)
    r = await db.execute(
        select(func.count(Deal.id)).where(_deal_in_stats(), Deal.status == "negotiating")
    )
    quoting_count = r.scalar() or 0

    # KPI 4: monthly GMV (won this month, by won_at)
    r = await db.execute(
        select(func.coalesce(func.sum(Deal.amount), 0)).where(
            _deal_in_stats(),
            Deal.status == "won",
            extract("year", Deal.won_at) == year,
            extract("month", Deal.won_at) == month,
        )
    )
    monthly_gmv = float(r.scalar() or 0)

    # KPI 5: monthly win rate (deals created this month)
    r = await db.execute(
        select(
            func.count(case((Deal.status == "won", 1))),
            func.count(Deal.id),
        ).where(
            _deal_in_stats(),
            extract("year", Deal.created_at) == year,
            extract("month", Deal.created_at) == month,
        )
    )
    won, total_month = r.one()
    monthly_win_rate = round(won / total_month * 100, 1) if total_month else 0

    # KPI 6: pipeline value (following + negotiating)
    r = await db.execute(
        select(func.coalesce(func.sum(Deal.amount), 0)).where(
            _deal_in_stats(),
            Deal.status.in_(["following", "negotiating"]),
        )
    )
    pipeline_value = float(r.scalar() or 0)

    kpis = [
        {"key": "new_leads_today", "value": new_leads_today},
        {"key": "follow_up_today", "value": follow_up_today},
        {"key": "quoting_count", "value": quoting_count},
        {"key": "monthly_gmv", "value": monthly_gmv},
        {"key": "monthly_win_rate", "value": monthly_win_rate},
        {"key": "pipeline_value", "value": pipeline_value},
    ]

    funnel = await _build_funnel(db)

    return {"kpis": kpis, "funnel": funnel}


async def _build_funnel(db: AsyncSession, scope_ids: Optional[list[str]] = None) -> list[dict]:
    """Build 5-stage funnel. If scope_ids given, filter by Deal.assigned_to."""

    def scope(stmt):
        if scope_ids is not None:
            return stmt.where(Deal.assigned_to.in_(scope_ids))
        return stmt

    stages = []

    for status_val, stage_key in [
        ("lead", "newLead"),
        ("following", "contacted"),
        ("negotiating", "quoting"),
        ("won", "won"),
    ]:
        r = await db.execute(
            scope(
                select(
                    func.count(Deal.id),
                    func.coalesce(func.sum(Deal.amount), 0),
                ).where(_deal_in_stats(), Deal.status == status_val)
            )
        )
        cnt, amt = r.one()
        stages.append({"stage": stage_key, "count": cnt, "amount": float(amt)})

    # Insert "qualified" between contacted and quoting:
    # following deals whose contact has >=1 activity
    activity_subq = (
        select(Activity.contact_id)
        .group_by(Activity.contact_id)
        .having(func.count(Activity.id) >= 1)
    ).subquery()

    r = await db.execute(
        scope(
            select(
                func.count(Deal.id),
                func.coalesce(func.sum(Deal.amount), 0),
            ).where(
                _deal_in_stats(),
                Deal.status == "following",
                Deal.contact_id.in_(select(activity_subq.c.contact_id)),
            )
        )
    )
    cnt, amt = r.one()
    # Insert "qualified" after index 1 (after "contacted")
    stages.insert(2, {"stage": "qualified", "count": cnt, "amount": float(amt)})

    return stages


async def _build_manager_funnel(db: AsyncSession, scope_ids: list[str]) -> list[dict]:
    """Build 6-stage funnel for manager (adds 'negotiating' stage)."""
    stages = await _build_funnel(db, scope_ids)

    # negotiating deals whose contact has >=2 activities
    activity_subq = (
        select(Activity.contact_id)
        .group_by(Activity.contact_id)
        .having(func.count(Activity.id) >= 2)
    ).subquery()

    r = await db.execute(
        select(
            func.count(Deal.id),
            func.coalesce(func.sum(Deal.amount), 0),
        ).where(
            _deal_in_stats(),
            Deal.status == "negotiating",
            Deal.assigned_to.in_(scope_ids),
            Deal.contact_id.in_(select(activity_subq.c.contact_id)),
        )
    )
    cnt, amt = r.one()
    negotiating = {"stage": "negotiating", "count": cnt, "amount": float(amt)}

    # Insert before the last stage (won) — stages order: newLead, contacted, qualified, quoting, <negotiating>, won
    stages.insert(-1, negotiating)
    return stages


# ---------------------------------------------------------------------------
# Manager Dashboard
# ---------------------------------------------------------------------------


async def get_manager_dashboard(db: AsyncSession, user: User) -> dict:
    team_ids = await _get_team_ids(db, user.id)
    now = datetime.utcnow()
    year, month = now.year, now.month

    # KPI 1: team monthly GMV (won this month by won_at)
    r = await db.execute(
        select(func.coalesce(func.sum(Deal.amount), 0)).where(
            _deal_in_stats(),
            Deal.status == "won",
            Deal.assigned_to.in_(team_ids),
            extract("year", Deal.won_at) == year,
            extract("month", Deal.won_at) == month,
        )
    )
    team_monthly_gmv = float(r.scalar() or 0)

    # KPI 2: target completion rate
    r = await db.execute(
        select(func.coalesce(func.sum(SalesTarget.target_amount), 0)).where(
            SalesTarget.user_id.in_(team_ids),
            SalesTarget.year == year,
            SalesTarget.month == month,
        )
    )
    team_target = float(r.scalar() or 0)
    team_target_rate = round(team_monthly_gmv / team_target * 100, 1) if team_target else 0

    # KPI 3: team pipeline value (following + negotiating)
    r = await db.execute(
        select(func.coalesce(func.sum(Deal.amount), 0)).where(
            _deal_in_stats(),
            Deal.status.in_(["following", "negotiating"]),
            Deal.assigned_to.in_(team_ids),
        )
    )
    team_pipeline_value = float(r.scalar() or 0)

    # KPI 4: team win rate (deals created this month)
    r = await db.execute(
        select(
            func.count(case((Deal.status == "won", 1))),
            func.count(Deal.id),
        ).where(
            _deal_in_stats(),
            Deal.assigned_to.in_(team_ids),
            extract("year", Deal.created_at) == year,
            extract("month", Deal.created_at) == month,
        )
    )
    won, total = r.one()
    team_win_rate = round(won / total * 100, 1) if total else 0

    # KPI 5: avg sales cycle (last 90 days, won deals)
    cutoff = now - timedelta(days=90)
    r = await db.execute(
        select(func.avg(func.datediff(Deal.won_at, Deal.created_at))).where(
            _deal_in_stats(),
            Deal.status == "won",
            Deal.assigned_to.in_(team_ids),
            Deal.won_at >= cutoff,
        )
    )
    avg_cycle = r.scalar()
    avg_sales_cycle_days = round(float(avg_cycle), 1) if avg_cycle else 0

    kpis = [
        {"key": "team_monthly_gmv", "value": team_monthly_gmv},
        {"key": "team_target_rate", "value": team_target_rate},
        {"key": "team_pipeline_value", "value": team_pipeline_value},
        {"key": "team_win_rate", "value": team_win_rate},
        {"key": "avg_sales_cycle_days", "value": avg_sales_cycle_days},
    ]

    funnel = await _build_manager_funnel(db, team_ids)

    return {"kpis": kpis, "funnel": funnel}


# ---------------------------------------------------------------------------
# Sales Dashboard
# ---------------------------------------------------------------------------


async def get_sales_dashboard(db: AsyncSession, user: User) -> dict:
    uid = user.id
    today = date.today()
    now = datetime.utcnow()
    year, month = now.year, now.month

    # KPI 1: new contacts today (assigned to this user)
    r = await db.execute(
        select(func.count(Contact.id)).where(
            _contact_in_stats(),
            Contact.assigned_to == uid,
            func.date(Contact.created_at) == today,
        )
    )
    new_contacts_today = r.scalar() or 0

    # KPI 2: follow-up today
    r = await db.execute(
        select(func.count(Task.id)).where(
            Task.assigned_to == uid,
            Task.due_date == today,
            Task.is_done == False,
        )
    )
    follow_up_today = r.scalar() or 0

    # KPI 3: monthly GMV (won this month by won_at)
    r = await db.execute(
        select(func.coalesce(func.sum(Deal.amount), 0)).where(
            _deal_in_stats(),
            Deal.status == "won",
            Deal.assigned_to == uid,
            extract("year", Deal.won_at) == year,
            extract("month", Deal.won_at) == month,
        )
    )
    monthly_gmv = float(r.scalar() or 0)

    # KPI 4: monthly won count
    r = await db.execute(
        select(func.count(Deal.id)).where(
            _deal_in_stats(),
            Deal.status == "won",
            Deal.assigned_to == uid,
            extract("year", Deal.won_at) == year,
            extract("month", Deal.won_at) == month,
        )
    )
    monthly_won_count = r.scalar() or 0

    # KPI 5: target completion rate
    r = await db.execute(
        select(SalesTarget.target_amount).where(
            SalesTarget.user_id == uid,
            SalesTarget.year == year,
            SalesTarget.month == month,
        )
    )
    target = r.scalar()
    target_rate = round(monthly_gmv / float(target) * 100, 1) if target else 0

    kpis = [
        {"key": "new_contacts_today", "value": new_contacts_today},
        {"key": "follow_up_today", "value": follow_up_today},
        {"key": "monthly_gmv", "value": monthly_gmv},
        {"key": "monthly_won_count", "value": monthly_won_count},
        {"key": "target_completion_rate", "value": target_rate},
    ]

    # Pipeline: group by deal status (for this user)
    r = await db.execute(
        select(
            Deal.status,
            func.count(Deal.id),
            func.coalesce(func.sum(Deal.amount), 0),
            func.max(Deal.updated_at),
        )
        .where(
            _deal_in_stats(),
            Deal.assigned_to == uid,
        )
        .group_by(Deal.status)
    )
    rows = r.all()
    status_map = {row[0]: row for row in rows}

    stage_order = [
        ("lead", "newLead"),
        ("following", "contacted"),
        ("negotiating", "quoting"),
        ("won", "won"),
        ("lost", "lost"),
    ]
    pipeline = []
    for status_val, stage_key in stage_order:
        row = status_map.get(status_val)
        if row:
            pipeline.append(
                {
                    "stage": stage_key,
                    "count": row[1],
                    "amount": float(row[2]),
                    "last_updated": row[3].isoformat() if row[3] else None,
                }
            )
        else:
            pipeline.append(
                {
                    "stage": stage_key,
                    "count": 0,
                    "amount": 0,
                    "last_updated": None,
                }
            )

    return {"kpis": kpis, "pipeline": pipeline}


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


async def get_leaderboard(db: AsyncSession, month_str: str) -> dict:
    """month_str format: YYYY-MM"""
    year, month = _parse_month(month_str)

    r = await db.execute(
        select(
            Deal.assigned_to,
            User.name,
            User.avatar_url,
            func.coalesce(func.sum(Deal.amount), 0).label("deal_amount"),
            func.count(Deal.id).label("deal_count"),
        )
        .join(User, User.id == Deal.assigned_to)
        .where(
            _deal_in_stats(),
            Deal.status == "won",
            extract("year", Deal.won_at) == year,
            extract("month", Deal.won_at) == month,
        )
        .group_by(Deal.assigned_to, User.name, User.avatar_url)
        .order_by(func.sum(Deal.amount).desc())
        .limit(10)
    )
    rows = r.all()

    entries = []
    for i, row in enumerate(rows, 1):
        entries.append(
            {
                "rank": i,
                "user_id": row[0],
                "user_name": row[1],
                "avatar_url": row[2],
                "deal_amount": float(row[3]),
                "deal_count": row[4],
                "win_rate": 0,
            }
        )

    if entries:
        user_ids = [e["user_id"] for e in entries]
        r = await db.execute(
            select(
                Deal.assigned_to,
                func.count(case((Deal.status == "won", 1))),
                func.count(Deal.id),
            )
            .where(
                _deal_in_stats(),
                Deal.assigned_to.in_(user_ids),
                extract("year", Deal.created_at) == year,
                extract("month", Deal.created_at) == month,
            )
            .group_by(Deal.assigned_to)
        )
        wr_map = {}
        for uid, won, total in r.all():
            wr_map[uid] = round(won / total * 100, 1) if total else 0
        for e in entries:
            e["win_rate"] = wr_map.get(e["user_id"], 0)

    return {"month": month_str, "entries": entries}


async def get_team_leaderboard(db: AsyncSession, user: User, month_str: str) -> dict:
    team_ids = await _get_team_ids(db, user.id)
    year, month = _parse_month(month_str)

    r = await db.execute(
        select(
            Deal.assigned_to,
            User.name,
            User.avatar_url,
            func.coalesce(func.sum(Deal.amount), 0).label("deal_amount"),
            func.count(Deal.id).label("deal_count"),
        )
        .join(User, User.id == Deal.assigned_to)
        .where(
            _deal_in_stats(),
            Deal.status == "won",
            Deal.assigned_to.in_(team_ids),
            extract("year", Deal.won_at) == year,
            extract("month", Deal.won_at) == month,
        )
        .group_by(Deal.assigned_to, User.name, User.avatar_url)
        .order_by(func.sum(Deal.amount).desc())
    )
    rows = r.all()

    wr_result = await db.execute(
        select(
            Deal.assigned_to,
            func.count(case((Deal.status == "won", 1))),
            func.count(Deal.id),
        )
        .where(
            _deal_in_stats(),
            Deal.assigned_to.in_(team_ids),
            extract("year", Deal.created_at) == year,
            extract("month", Deal.created_at) == month,
        )
        .group_by(Deal.assigned_to)
    )
    wr_map = {}
    for uid, won, total in wr_result.all():
        wr_map[uid] = round(won / total * 100, 1) if total else 0

    entries = []
    for i, row in enumerate(rows, 1):
        entries.append(
            {
                "rank": i,
                "user_id": row[0],
                "user_name": row[1],
                "avatar_url": row[2],
                "deal_amount": float(row[3]),
                "deal_count": row[4],
                "win_rate": wr_map.get(row[0], 0),
            }
        )

    return {"month": month_str, "entries": entries}


# ---------------------------------------------------------------------------
# GMV Trend
# ---------------------------------------------------------------------------


async def get_gmv_trend(db: AsyncSession, period: str) -> dict:
    now = datetime.utcnow()

    if period == "year":
        start_year = now.year - 4
        r = await db.execute(
            select(
                extract("year", Deal.won_at).label("yr"),
                func.coalesce(func.sum(Deal.amount), 0),
            )
            .where(
                _deal_in_stats(),
                Deal.status == "won",
                Deal.won_at.isnot(None),
                extract("year", Deal.won_at) >= start_year,
            )
            .group_by("yr")
            .order_by("yr")
        )
        data = [{"label": str(int(row[0])), "value": float(row[1])} for row in r.all()]
    else:
        # last 12 months
        cutoff = now - timedelta(days=365)
        r = await db.execute(
            select(
                func.date_format(Deal.won_at, "%Y-%m").label("ym"),
                func.coalesce(func.sum(Deal.amount), 0),
            )
            .where(
                _deal_in_stats(),
                Deal.status == "won",
                Deal.won_at.isnot(None),
                Deal.won_at >= cutoff,
            )
            .group_by("ym")
            .order_by("ym")
        )
        data = [{"label": row[0], "value": float(row[1])} for row in r.all()]

    return {"period": period, "data": data}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_month(month_str: str) -> tuple[int, int]:
    parts = month_str.split("-")
    return int(parts[0]), int(parts[1])
