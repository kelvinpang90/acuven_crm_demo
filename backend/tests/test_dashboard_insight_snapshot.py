"""CRM-TASK-040 acceptance tests for the manager dashboard team snapshot."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.deal import Deal
from app.models.sales_target import SalesTarget
from app.models.user import User
from app.services.dashboard_insight_service import build_team_snapshot

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
BOSS = "boss"
OLD = datetime(2026, 8, 1)


def _user(user_id, name=None, *, manager_id=BOSS, is_active=True, role="sales"):
    return User(
        id=user_id,
        name=name or user_id,
        email=f"{user_id}@example.test",
        password_hash="hash",
        role=role,
        manager_id=manager_id,
        is_active=is_active,
    )


def _manager(user_id=BOSS, name="Boss"):
    return _user(user_id, name, manager_id=None, role="manager")


def _deal(assigned_to, status, amount="0.00", *, created_at=OLD, won_at=None, deleted_at=None):
    return Deal(
        contact_id="contact",
        assigned_to=assigned_to,
        status=status,
        amount=Decimal(amount),
        created_at=created_at,
        won_at=won_at,
        deleted_at=deleted_at,
    )


def _won(assigned_to, amount, won_at, *, created_at=OLD):
    return _deal(assigned_to, "won", amount, created_at=created_at, won_at=won_at)


def _target(user_id, amount, year=2026, month=10):
    return SalesTarget(user_id=user_id, year=year, month=month, target_amount=Decimal(amount))


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _snapshot(maker, manager_id=BOSS, now=NOW):
    async with maker() as session:
        manager = await session.get(User, manager_id)
        return await build_team_snapshot(session, manager=manager, now=now)


def _member(snapshot, name):
    return next(member for member in snapshot["members"] if member["name"] == name)


@pytest.mark.asyncio
async def test_top_level_keys_period_and_january_previous_month(async_session_maker):
    """Guards criteria 1 and 2: four top-level keys, the period block, and Jan -> previous Dec."""
    await _seed(
        async_session_maker,
        _manager(),
        _won(BOSS, "100.00", datetime(2026, 12, 31, 23, 59, 59)),
        _target(BOSS, "400.00", 2026, 12),
    )
    snapshot = await _snapshot(async_session_maker)
    assert set(snapshot) == {"period", "team", "members", "truncated"}
    assert snapshot["period"] == {
        "month": "2026-10",
        "as_of": "2026-10-18",
        "day_of_month": 18,
        "days_in_month": 31,
    }

    january = await _snapshot(async_session_maker, now=datetime(2027, 1, 3, 8, tzinfo=timezone.utc))
    assert january["period"] == {
        "month": "2027-01",
        "as_of": "2027-01-03",
        "day_of_month": 3,
        "days_in_month": 31,
    }
    assert january["team"]["last_month"]["won_amount"] == "100.00"
    assert january["team"]["last_month"]["target_amount"] == "400.00"
    assert january["team"]["last_month"]["target_rate"] == 25.0
    assert january["team"]["this_month"]["won_amount"] == "0.00"


@pytest.mark.asyncio
async def test_team_scope_and_deleted_deals(async_session_maker):
    """Guards criterion 2: team is the manager plus direct reports; deleted deals are ignored."""
    now = NOW.replace(tzinfo=None)
    await _seed(
        async_session_maker,
        _manager(),
        _user("ali", "Ali"),
        _manager("boss2", "Boss2"),
        _user("xin", "Xin", manager_id="boss2"),
        _user("lone", "Lone", manager_id=None),
        _won(BOSS, "100.00", now),
        _won("ali", "200.00", now),
        _deal("ali", "won", "1000.00", won_at=now, deleted_at=now),
        _deal("ali", "lead", "50.00", deleted_at=now),
        _won("xin", "5000.00", now),
        _won("boss2", "7000.00", now),
        _won("lone", "9000.00", now),
    )
    snapshot = await _snapshot(async_session_maker)
    assert {member["name"] for member in snapshot["members"]} == {"Boss", "Ali"}
    assert _member(snapshot, "Boss")["is_manager"] is True
    assert _member(snapshot, "Ali")["is_manager"] is False
    assert snapshot["team"]["this_month"]["won_amount"] == "300.00"
    assert snapshot["team"]["this_month"]["won_count"] == 2
    assert snapshot["team"]["open_deals"]["lead"] == {"count": 0, "amount": "0.00"}


@pytest.mark.asyncio
async def test_month_boundaries_for_won_at_and_created_at(async_session_maker):
    """Guards criterion 3: UTC month boundaries for won_at and created_at."""
    first = datetime(2026, 10, 1, 0, 0, 0)
    last_second = datetime(2026, 9, 30, 23, 59, 59)
    await _seed(
        async_session_maker,
        _manager(),
        _won(BOSS, "100.00", first),
        _won(BOSS, "200.00", last_second),
        _deal(BOSS, "lead", created_at=first),
        _deal(BOSS, "lead", created_at=last_second),
    )
    snapshot = await _snapshot(async_session_maker)
    boss = _member(snapshot, "Boss")
    assert boss["this_month"]["won_amount"] == "100.00"
    assert boss["this_month"]["won_count"] == 1
    assert boss["last_month"]["won_amount"] == "200.00"
    assert boss["last_month"]["won_count"] == 1
    assert boss["this_month"]["created_count"] == 1
    assert snapshot["team"]["last_month"]["created_count"] == 1


@pytest.mark.asyncio
async def test_targets_rates_and_remaining(async_session_maker):
    """Guards criteria 3 and 5: target sums, null rates without targets, remaining floored at 0."""
    now = NOW.replace(tzinfo=None)
    await _seed(
        async_session_maker,
        _manager(),
        _user("ali", "Ali"),
        _user("bea", "Bea"),
        _target("ali", "3000.00"),
        _target("bea", "2000.00"),
        _won("ali", "1000.00", now),
        _won("bea", "2500.00", now),
        _manager("boss2", "Boss2"),
        _won("boss2", "800.00", now),
        _manager("boss3", "Boss3"),
        _target("boss3", "500.00"),
        _won("boss3", "900.00", now),
    )
    snapshot = await _snapshot(async_session_maker)
    assert _member(snapshot, "Ali")["this_month"]["target_rate"] == 33.3
    assert _member(snapshot, "Bea")["this_month"]["target_rate"] == 125.0
    boss = _member(snapshot, "Boss")["this_month"]
    assert boss["target_amount"] == "0.00"
    assert boss["target_rate"] is None
    team = snapshot["team"]
    assert team["this_month"]["target_amount"] == "5000.00"
    assert team["this_month"]["target_rate"] == 70.0
    assert team["remaining_to_target"] == "1500.00"
    assert team["last_month"]["target_rate"] is None

    no_target = (await _snapshot(async_session_maker, "boss2"))["team"]
    assert no_target["this_month"]["target_amount"] == "0.00"
    assert no_target["this_month"]["target_rate"] is None
    assert no_target["remaining_to_target"] is None

    over_target = (await _snapshot(async_session_maker, "boss3"))["team"]
    assert over_target["this_month"]["target_rate"] == 180.0
    assert over_target["remaining_to_target"] == "0.00"


@pytest.mark.asyncio
async def test_win_rate_by_creation_month(async_session_maker):
    """Guards criterion 5 (team win_rate): 1 of 3 created this month won is 33.3; none is null."""
    created = datetime(2026, 10, 5)
    await _seed(
        async_session_maker,
        _manager(),
        _user("ali", "Ali"),
        _won("ali", "100.00", datetime(2026, 10, 6), created_at=created),
        _deal("ali", "lead", created_at=created),
        _deal("ali", "lost", created_at=created),
    )
    snapshot = await _snapshot(async_session_maker)
    ali = _member(snapshot, "Ali")["this_month"]
    assert ali["created_count"] == 3
    assert ali["created_won_count"] == 1
    assert snapshot["team"]["this_month"]["created_count"] == 3
    assert snapshot["team"]["this_month"]["created_won_count"] == 1
    assert snapshot["team"]["this_month"]["win_rate"] == 33.3
    assert snapshot["team"]["last_month"]["created_count"] == 0
    assert snapshot["team"]["last_month"]["win_rate"] is None


@pytest.mark.asyncio
async def test_open_deals_by_status(async_session_maker):
    """Guards criterion 3 (open_deals): count and amount per open status; won and lost excluded."""
    await _seed(
        async_session_maker,
        _manager(),
        _user("ali", "Ali"),
        _deal("ali", "lead", "100.00"),
        _deal("ali", "lead", "50.00"),
        _deal("ali", "following", "200.00"),
        _deal("ali", "negotiating", "300.00"),
        _won("ali", "999.00", OLD),
        _deal("ali", "lost", "888.00"),
        _deal(BOSS, "lead", "10.00"),
    )
    snapshot = await _snapshot(async_session_maker)
    assert _member(snapshot, "Ali")["open_deals"] == {
        "lead": {"count": 2, "amount": "150.00"},
        "following": {"count": 1, "amount": "200.00"},
        "negotiating": {"count": 1, "amount": "300.00"},
    }
    assert snapshot["team"]["open_deals"] == {
        "lead": {"count": 3, "amount": "160.00"},
        "following": {"count": 1, "amount": "200.00"},
        "negotiating": {"count": 1, "amount": "300.00"},
    }


@pytest.mark.asyncio
async def test_avg_sales_cycle_days(async_session_maker):
    """Guards criterion 5 (avg_sales_cycle_days): date difference average over the last 90 days."""
    now = NOW.replace(tzinfo=None)
    await _seed(
        async_session_maker,
        _manager(),
        _user("ali", "Ali"),
        _won("ali", "100.00", datetime(2026, 10, 11, 1), created_at=datetime(2026, 10, 1, 23)),
        _won("ali", "100.00", datetime(2026, 9, 22, 8), created_at=datetime(2026, 9, 1, 9)),
        _won("ali", "100.00", now - timedelta(days=91), created_at=now - timedelta(days=200)),
        _manager("boss2", "Boss2"),
    )
    snapshot = await _snapshot(async_session_maker)
    assert snapshot["team"]["avg_sales_cycle_days"] == 15.5
    empty = await _snapshot(async_session_maker, "boss2")
    assert empty["team"]["avg_sales_cycle_days"] is None


@pytest.mark.asyncio
async def test_members_include_zero_active_and_exact_keys(async_session_maker):
    """Guards criteria 4 and 6: active zero members are listed, keys and formats are exact."""
    await _seed(async_session_maker, _manager(), _user("zed", "Zed"))
    snapshot = await _snapshot(async_session_maker)
    zed = _member(snapshot, "Zed")
    assert set(zed) == {
        "name",
        "is_manager",
        "this_month",
        "last_month",
        "open_deals",
        "stalled_deals",
    }
    assert zed["this_month"] == {
        "won_amount": "0.00",
        "won_count": 0,
        "target_amount": "0.00",
        "target_rate": None,
        "created_count": 0,
        "created_won_count": 0,
    }
    assert zed["last_month"] == {
        "won_amount": "0.00",
        "won_count": 0,
        "target_amount": "0.00",
        "target_rate": None,
    }
    assert zed["open_deals"] == {
        status: {"count": 0, "amount": "0.00"} for status in ("lead", "following", "negotiating")
    }
    assert set(snapshot["team"]) == {
        "this_month",
        "last_month",
        "open_deals",
        "stalled_deals",
        "avg_sales_cycle_days",
        "remaining_to_target",
    }
    assert snapshot["truncated"] is False


@pytest.mark.asyncio
async def test_inactive_members_listed_only_with_figures(async_session_maker):
    """Guards criteria 4 and 5: idle inactive users are hidden but still count towards the team."""
    await _seed(
        async_session_maker,
        _manager(),
        _user("gone", "Gone", is_active=False),
        _target("gone", "5000.00"),
        _user("parked", "Parked", is_active=False),
        _deal("parked", "following", "70.00"),
    )
    snapshot = await _snapshot(async_session_maker)
    names = {member["name"] for member in snapshot["members"]}
    assert "Gone" not in names
    assert "Parked" in names
    assert snapshot["team"]["this_month"]["target_amount"] == "5000.00"


@pytest.mark.asyncio
async def test_members_capped_at_thirty_with_full_team_totals(async_session_maker):
    """Guards criteria 4 and 5: 31 people list 30, truncated is true, totals include the 31st."""
    now = NOW.replace(tzinfo=None)
    reports = [_user(f"s{index:02}", f"S{index:02}") for index in range(30)]
    wins = [_won(f"s{index:02}", f"{(index + 1) * 10}.00", now) for index in range(30)]
    await _seed(async_session_maker, _manager(), *reports, *wins, _won(BOSS, "1.00", now))
    snapshot = await _snapshot(async_session_maker)
    assert len(snapshot["members"]) == 30
    assert snapshot["truncated"] is True
    assert "Boss" not in {member["name"] for member in snapshot["members"]}
    assert snapshot["team"]["this_month"]["won_amount"] == "4651.00"
    assert snapshot["team"]["this_month"]["won_count"] == 31


@pytest.mark.asyncio
async def test_members_sorted_by_won_amount_then_name(async_session_maker):
    """Guards criterion 4: this-month won_amount descending, ties by name ascending."""
    now = NOW.replace(tzinfo=None)
    await _seed(
        async_session_maker,
        _manager(),
        _user("cat", "Cat"),
        _user("ann", "Ann"),
        _user("bob", "Bob"),
        _won("cat", "100.00", now),
        _won("ann", "100.00", now),
        _won("bob", "300.00", now),
    )
    snapshot = await _snapshot(async_session_maker)
    assert [member["name"] for member in snapshot["members"]] == ["Bob", "Ann", "Cat", "Boss"]
    assert snapshot["truncated"] is False
