from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.deal import Deal
from app.models.message import Message
from app.models.user import User
from app.utils.response import ok

router = APIRouter()


async def _get_scoped_deal_conditions(current_user: User, db: AsyncSession) -> list:
    conditions = [Deal.deleted_at.is_(None)]
    if current_user.role == "sales":
        conditions.append(Deal.assigned_to == current_user.id)
    elif current_user.role == "manager":
        result = await db.execute(
            select(User.id).where(
                (User.manager_id == current_user.id) | (User.id == current_user.id)
            )
        )
        team_ids = [r[0] for r in result.all()]
        conditions.append(Deal.assigned_to.in_(team_ids))
    return conditions


@router.get("")
async def get_analytics(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=90, ge=7, le=365),
):
    scope = await _get_scoped_deal_conditions(current_user, db)
    since = datetime.utcnow() - timedelta(days=days)

    # Overview (all-time)
    overview_q = await db.execute(
        select(
            func.count(Deal.id).label("total"),
            func.sum(case((Deal.status == "won", 1), else_=0)).label("won"),
            func.sum(case((Deal.status == "lost", 1), else_=0)).label("lost"),
            func.coalesce(func.sum(case((Deal.status == "won", Deal.amount), else_=0)), 0).label(
                "deal_amount"
            ),
        ).where(*scope)
    )
    row = overview_q.one()
    total = row.total or 0
    won = int(row.won or 0)
    lost = int(row.lost or 0)
    deal_amount = float(row.deal_amount or 0)

    overview = {
        "total_contacts": total,
        "total_won": won,
        "total_lost": lost,
        "overall_conversion_rate": round(won / total * 100, 1) if total > 0 else 0,
        "total_deal_amount": deal_amount,
        "avg_deal_value": round(deal_amount / won, 2) if won > 0 else 0,
    }

    # Conversion trend: new deals by created_at + won deals by won_at
    new_q = await db.execute(
        select(
            func.date_format(Deal.created_at, "%Y-%m-%d").label("dt"),
            func.count(Deal.id).label("cnt"),
        )
        .where(*scope, Deal.created_at >= since)
        .group_by("dt")
        .order_by("dt")
    )
    won_q = await db.execute(
        select(
            func.date_format(Deal.won_at, "%Y-%m-%d").label("dt"),
            func.count(Deal.id).label("cnt"),
        )
        .where(*scope, Deal.won_at.isnot(None), Deal.won_at >= since)
        .group_by("dt")
        .order_by("dt")
    )
    new_by_date: dict[str, int] = {r.dt: r.cnt for r in new_q.all()}
    won_by_date: dict[str, int] = {r.dt: r.cnt for r in won_q.all()}
    all_dates = sorted(set(new_by_date) | set(won_by_date))
    conversion_trend = [
        {
            "date": dt,
            "new_contacts": new_by_date.get(dt, 0),
            "won": won_by_date.get(dt, 0),
        }
        for dt in all_dates
    ]

    # Channel distribution (messages)
    channel_q = await db.execute(
        select(
            Message.channel,
            func.count(Message.id).label("cnt"),
        )
        .where(Message.created_at >= since)
        .group_by(Message.channel)
    )
    channels_raw = channel_q.all()
    ch_total = sum(r.cnt for r in channels_raw) or 1
    channel_distribution = [
        {
            "channel": r.channel,
            "count": r.cnt,
            "percentage": round(r.cnt / ch_total * 100, 1),
        }
        for r in channels_raw
    ]

    # Sales ranking: deals created in window, filter to those with at least 1 won
    ranking_q = await db.execute(
        select(
            Deal.assigned_to,
            func.count(Deal.id).label("total_count"),
            func.sum(case((Deal.won_at.isnot(None), 1), else_=0)).label("won_count"),
            func.coalesce(func.sum(case((Deal.won_at.isnot(None), Deal.amount), else_=0)), 0).label(
                "deal_amount"
            ),
        )
        .where(*scope, Deal.assigned_to.isnot(None), Deal.created_at >= since)
        .group_by(Deal.assigned_to)
        .having(func.sum(case((Deal.won_at.isnot(None), 1), else_=0)) > 0)
        .order_by(func.sum(case((Deal.won_at.isnot(None), Deal.amount), else_=0)).desc())
    )
    rankings_raw = ranking_q.all()

    user_ids = [r.assigned_to for r in rankings_raw if r.assigned_to]
    user_names: dict[str, str] = {}
    if user_ids:
        u_q = await db.execute(select(User.id, User.name).where(User.id.in_(user_ids)))
        user_names = {uid: name for uid, name in u_q.all()}

    sales_ranking = []
    for r in rankings_raw:
        tc = r.total_count or 0
        wc = r.won_count or 0
        sales_ranking.append(
            {
                "user_id": r.assigned_to,
                "user_name": user_names.get(r.assigned_to, ""),
                "deal_count": wc,
                "deal_amount": float(r.deal_amount or 0),
                "conversion_rate": round(wc / tc * 100, 1) if tc > 0 else 0,
            }
        )

    return ok(
        data={
            "overview": overview,
            "conversion_trend": conversion_trend,
            "channel_distribution": channel_distribution,
            "sales_ranking": sales_ranking,
        }
    )
