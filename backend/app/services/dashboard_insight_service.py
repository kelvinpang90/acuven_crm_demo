"""Team snapshot assembly for the manager dashboard AI insight."""

import calendar
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.deal import Deal
from app.models.message import Message
from app.models.sales_target import SalesTarget
from app.models.user import User
from app.services import ai_api_client
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    INVALID_INPUT,
    LANGUAGES,
    NO_DATA,
    NOT_CONFIGURED,
    OK,
    AiAssistResult,
    write_ai_request,
)
from app.services.ai_features import (
    DASHBOARD_INSIGHT_FEATURE,
    DASHBOARD_INSIGHT_SCHEMA,
    DASHBOARD_INSIGHT_SYSTEM,
    MAX_TOKENS,
    validate_dashboard_insight,
)
from app.services.deal_next_step_service import STALL_DAYS

OPEN_STATUSES = ("lead", "following", "negotiating")
MEMBER_LIMIT = 30
SALES_CYCLE_DAYS = 90


def _as_utc(value: datetime) -> datetime:
    """Treat naive database timestamps as UTC."""
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _month_key(value: datetime) -> tuple[int, int]:
    value = _as_utc(value)
    return value.year, value.month


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _amount(value: Decimal) -> str:
    return format(Decimal(value), ".2f")


def _one_decimal(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _percent(numerator: Decimal | int, denominator: Decimal | int) -> float | None:
    if not denominator:
        return None
    return _one_decimal(Decimal(numerator) * 100 / Decimal(denominator))


def _empty_month() -> dict[str, Any]:
    return {
        "won_amount": Decimal("0.00"),
        "won_count": 0,
        "target_amount": Decimal("0.00"),
        "created_count": 0,
        "created_won_count": 0,
    }


def _empty_open() -> dict[str, dict[str, Any]]:
    return {status: {"count": 0, "amount": Decimal("0.00")} for status in OPEN_STATUSES}


def _format_open(open_deals: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        status: {"count": values["count"], "amount": _amount(values["amount"])}
        for status, values in open_deals.items()
    }


def _member_month(stats: dict[str, Any], *, with_created: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "won_amount": _amount(stats["won_amount"]),
        "won_count": stats["won_count"],
        "target_amount": _amount(stats["target_amount"]),
        "target_rate": _percent(stats["won_amount"], stats["target_amount"]),
    }
    if with_created:
        item["created_count"] = stats["created_count"]
        item["created_won_count"] = stats["created_won_count"]
    return item


def _team_month(stats: dict[str, Any]) -> dict[str, Any]:
    item = _member_month(stats, with_created=True)
    item["win_rate"] = _percent(stats["created_won_count"], stats["created_count"])
    return item


async def _latest_progress(
    db: AsyncSession, team_ids: list[str]
) -> tuple[dict[str, datetime], dict[str, datetime]]:
    """Latest follow-up per open deal and latest message per their contacts, two grouped queries."""
    open_filter = (
        Deal.deleted_at.is_(None),
        Deal.assigned_to.in_(team_ids),
        Deal.status.in_(OPEN_STATUSES),
    )
    activities = await db.execute(
        select(Activity.deal_id, func.max(Activity.follow_date))
        .where(Activity.deal_id.in_(select(Deal.id).where(*open_filter)))
        .group_by(Activity.deal_id)
    )
    messages = await db.execute(
        select(Message.contact_id, func.max(Message.created_at))
        .where(Message.contact_id.in_(select(Deal.contact_id).where(*open_filter)))
        .group_by(Message.contact_id)
    )
    return dict(activities.all()), dict(messages.all())


def _is_stalled(now: datetime, created_at: datetime, *progress: datetime | None) -> bool:
    """Same rule as deal_next_step_service: whole idle days since the latest progress."""
    last = max(_as_utc(value) for value in (created_at, *progress) if value is not None)
    return max(0, (now - last).days) >= STALL_DAYS


async def build_team_snapshot(db: AsyncSession, *, manager: User, now: datetime) -> dict[str, Any]:
    """Build the manager's team figures for this and last UTC month; no AI, no writes."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)

    this_key = (now.year, now.month)
    last_key = _previous_month(*this_key)
    months = {this_key: "this_month", last_key: "last_month"}
    last_start = datetime(last_key[0], last_key[1], 1)
    cycle_from = now - timedelta(days=SALES_CYCLE_DAYS)
    earliest = min(last_start, cycle_from.replace(tzinfo=None))

    reports = (
        (
            await db.execute(
                select(User)
                .where(User.manager_id == manager.id, User.id != manager.id)
                .order_by(User.id)
            )
        )
        .scalars()
        .all()
    )
    team = [manager, *reports]
    team_ids = [user.id for user in team]
    stats = {
        user.id: {
            "this_month": _empty_month(),
            "last_month": _empty_month(),
            "open_deals": _empty_open(),
            "stalled_deals": _empty_open(),
        }
        for user in team
    }

    targets = await db.execute(
        select(
            SalesTarget.user_id, SalesTarget.year, SalesTarget.month, SalesTarget.target_amount
        ).where(
            SalesTarget.user_id.in_(team_ids),
            or_(
                *(
                    and_(SalesTarget.year == year, SalesTarget.month == month)
                    for year, month in months
                )
            ),
        )
    )
    for user_id, year, month, target_amount in targets.all():
        period = months.get((year, month))
        if period is not None:
            stats[user_id][period]["target_amount"] += Decimal(str(target_amount or 0))

    deals = await db.execute(
        select(
            Deal.id,
            Deal.contact_id,
            Deal.assigned_to,
            Deal.status,
            Deal.amount,
            Deal.won_at,
            Deal.created_at,
        ).where(
            Deal.deleted_at.is_(None),
            Deal.assigned_to.in_(team_ids),
            or_(
                Deal.status.in_(OPEN_STATUSES),
                Deal.won_at >= earliest,
                Deal.created_at >= last_start,
            ),
        )
    )
    latest_activity, latest_message = await _latest_progress(db, team_ids)
    cycle_days: list[int] = []
    for deal_id, contact_id, assigned_to, status, amount, won_at, created_at in deals.all():
        person = stats[assigned_to]
        amount = Decimal(str(amount or 0))
        if status in OPEN_STATUSES:
            person["open_deals"][status]["count"] += 1
            person["open_deals"][status]["amount"] += amount
            if _is_stalled(
                now, created_at, latest_activity.get(deal_id), latest_message.get(contact_id)
            ):
                person["stalled_deals"][status]["count"] += 1
                person["stalled_deals"][status]["amount"] += amount
        if status == "won" and won_at is not None:
            period = months.get(_month_key(won_at))
            if period is not None:
                person[period]["won_amount"] += amount
                person[period]["won_count"] += 1
            won_utc = _as_utc(won_at)
            if cycle_from <= won_utc <= now:
                cycle_days.append((won_utc.date() - _as_utc(created_at).date()).days)
        period = months.get(_month_key(created_at))
        if period is not None:
            person[period]["created_count"] += 1
            if status == "won":
                person[period]["created_won_count"] += 1

    team_totals = {"this_month": _empty_month(), "last_month": _empty_month()}
    team_open = _empty_open()
    team_stalled = _empty_open()
    for person in stats.values():
        for period in team_totals:
            for key, value in person[period].items():
                team_totals[period][key] += value
        for team_counts, key in ((team_open, "open_deals"), (team_stalled, "stalled_deals")):
            for status, values in person[key].items():
                team_counts[status]["count"] += values["count"]
                team_counts[status]["amount"] += values["amount"]

    def listed(user: User) -> bool:
        if user.is_active:
            return True
        person = stats[user.id]
        return any(
            person[period]["won_count"] > 0 or person[period]["created_count"] > 0
            for period in ("this_month", "last_month")
        ) or any(values["count"] > 0 for values in person["open_deals"].values())

    candidates = sorted(
        (user for user in team if listed(user)),
        key=lambda user: (-stats[user.id]["this_month"]["won_amount"], user.name),
    )
    members = [
        {
            "name": user.name,
            "is_manager": user.id == manager.id,
            "this_month": _member_month(stats[user.id]["this_month"], with_created=True),
            "last_month": _member_month(stats[user.id]["last_month"], with_created=False),
            "open_deals": _format_open(stats[user.id]["open_deals"]),
            "stalled_deals": _format_open(stats[user.id]["stalled_deals"]),
        }
        for user in candidates[:MEMBER_LIMIT]
    ]

    this_month = team_totals["this_month"]
    remaining = None
    if this_month["target_amount"]:
        remaining = _amount(max(this_month["target_amount"] - this_month["won_amount"], 0))
    avg_cycle = (
        _one_decimal(Decimal(sum(cycle_days)) / Decimal(len(cycle_days))) if cycle_days else None
    )

    return {
        "period": {
            "month": f"{now.year:04d}-{now.month:02d}",
            "as_of": now.date().isoformat(),
            "day_of_month": now.day,
            "days_in_month": calendar.monthrange(now.year, now.month)[1],
        },
        "team": {
            "this_month": _team_month(this_month),
            "last_month": _team_month(team_totals["last_month"]),
            "open_deals": _format_open(team_open),
            "stalled_deals": _format_open(team_stalled),
            "avg_sales_cycle_days": avg_cycle,
            "remaining_to_target": remaining,
        },
        "members": members,
        "truncated": len(candidates) > MEMBER_LIMIT,
    }


def _as_of(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _has_data(team: dict[str, Any]) -> bool:
    return any(
        team[period]["won_count"] > 0 or team[period]["created_count"] > 0
        for period in ("this_month", "last_month")
    ) or any(values["count"] > 0 for values in team["open_deals"].values())


async def generate_dashboard_insight(
    db: AsyncSession, *, user: User, output_language: str
) -> AiAssistResult:
    """Generate a validated team insight for a manager and audit every AI client call."""
    if user.role != "manager":
        raise ValueError("manager role required")
    if not isinstance(output_language, str) or output_language not in LANGUAGES:
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)
    now = datetime.now(timezone.utc)
    snapshot = await build_team_snapshot(db, manager=user, now=now)
    if not _has_data(snapshot["team"]):
        return AiAssistResult(NO_DATA)

    result = await ai_api_client.send_message(
        feature=DASHBOARD_INSIGHT_FEATURE,
        system=DASHBOARD_INSIGHT_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": compact_json(
                    {"as_of": _as_of(now), "output_language": output_language, "snapshot": snapshot}
                ),
            }
        ],
        max_tokens=MAX_TOKENS,
        schema=DASHBOARD_INSIGHT_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_dashboard_insight(result.output)
        if output is not None:
            await write_ai_request(
                db,
                user_id=user.id,
                contact_id=None,
                feature=DASHBOARD_INSIGHT_FEATURE,
                request_id=result.request_id,
                outcome=OK,
            )
            return AiAssistResult(
                OK, request_id=result.request_id, truncated=snapshot["truncated"], **output
            )
        outcome = ERROR
    else:
        outcome = result.outcome
    await write_ai_request(
        db,
        user_id=user.id,
        contact_id=None,
        feature=DASHBOARD_INSIGHT_FEATURE,
        request_id=result.request_id,
        outcome=outcome,
    )
    return AiAssistResult(outcome, request_id=result.request_id, truncated=snapshot["truncated"])
