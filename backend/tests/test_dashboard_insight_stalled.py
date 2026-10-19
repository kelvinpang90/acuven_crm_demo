"""CRM-TASK-041 acceptance tests for stalled deals in the manager dashboard team snapshot."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.user import User
from app.services.dashboard_insight_service import build_team_snapshot
from app.services.deal_next_step_service import build_deal_snapshot

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
NAIVE_NOW = NOW.replace(tzinfo=None)
DAY = timedelta(days=1)
LONG_AGO = NAIVE_NOW - 30 * DAY
BOSS = "boss"
STATUSES = ("lead", "following", "negotiating")


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


def _contact(contact_id):
    return Contact(id=contact_id, name=contact_id, created_at=LONG_AGO)


def _deal(
    deal_id,
    contact_id,
    amount,
    *,
    assigned_to="ali",
    status="lead",
    created_at=LONG_AGO,
    won_at=None,
):
    return Deal(
        id=deal_id,
        contact_id=contact_id,
        title=deal_id,
        status=status,
        priority="mid",
        amount=Decimal(amount),
        assigned_to=assigned_to,
        created_at=created_at,
        won_at=won_at,
    )


def _activity(activity_id, deal_id, contact_id, follow_date, activity_type="phone"):
    return Activity(
        id=activity_id,
        contact_id=contact_id,
        user_id="ali",
        deal_id=deal_id,
        type=activity_type,
        follow_date=follow_date,
    )


def _message(message_id, contact_id, created_at, direction="inbound"):
    return Message(
        id=message_id,
        contact_id=contact_id,
        channel="whatsapp",
        direction=direction,
        sender_id="sender",
        recipient_id="recipient",
        body="body",
        created_at=created_at,
    )


# Every lead deal in cases 1-3 has a distinct power-of-two amount, so the stalled lead amount
# tells exactly which deals the batch rule judged stalled.
def _case_created_only():
    return [
        _contact("c1a"),
        _contact("c1b"),
        _deal("d1a", "c1a", "1.00", created_at=NAIVE_NOW - 7 * DAY),
        _deal("d1b", "c1b", "2.00", created_at=NAIVE_NOW - 6 * DAY - timedelta(hours=23)),
    ]


def _case_activities():
    return [
        _contact("c2a"),
        _contact("c2b"),
        _contact("c2c"),
        _contact("c2d"),
        _deal("d2a", "c2a", "4.00"),
        _deal("d2b", "c2b", "8.00"),
        _deal("d2c", "c2c", "16.00"),
        _deal("d2d", "c2d", "32.00"),
        _activity("a2a", "d2a", "c2a", NAIVE_NOW - 3 * DAY),
        _activity("a2b", "d2b", "c2b", NAIVE_NOW - 3 * DAY, "status change"),
        _activity("a2c", "d2c", "c2c", NAIVE_NOW + 2 * DAY),
    ]


def _case_messages():
    return [
        _contact("c3in"),
        _contact("c3out"),
        _contact("c3other"),
        _contact("c3x"),
        _contact("c3s"),
        _deal("d3in", "c3in", "64.00"),
        _deal("d3out", "c3out", "128.00"),
        _deal("d3other", "c3other", "256.00"),
        _deal("d3s1", "c3s", "512.00"),
        _deal("d3s2", "c3s", "1024.00"),
        _message("m3in", "c3in", NAIVE_NOW - 2 * DAY, "inbound"),
        _message("m3out", "c3out", NAIVE_NOW - 2 * DAY, "outbound"),
        _message("m3x", "c3x", NAIVE_NOW - 2 * DAY),
        _activity("a3s1", "d3s1", "c3s", NAIVE_NOW - DAY),
    ]


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _snapshot(maker, manager_id=BOSS):
    async with maker() as session:
        manager = await session.get(User, manager_id)
        return await build_team_snapshot(session, manager=manager, now=NOW)


def _member(snapshot, name):
    return next(member for member in snapshot["members"] if member["name"] == name)


def _stalled_ids(stalled_lead, rows):
    """Decode the power-of-two stalled lead amount back to deal ids."""
    total = int(Decimal(stalled_lead["amount"]))
    ids = {deal.id for deal in rows if isinstance(deal, Deal) and int(deal.amount) & total}
    assert stalled_lead["count"] == len(ids)
    return ids


@pytest.mark.asyncio
async def test_created_seven_days_ago_is_stalled(async_session_maker):
    """Guards criterion 2 (case 1): only created_at counts; 7 days is stalled, 6d23h is not."""
    rows = _case_created_only()
    await _seed(async_session_maker, _manager(), _user("ali", "Ali"), *rows)
    snapshot = await _snapshot(async_session_maker)
    assert _stalled_ids(_member(snapshot, "Ali")["stalled_deals"]["lead"], rows) == {"d1a"}


@pytest.mark.asyncio
async def test_latest_follow_up_resets_idle_days(async_session_maker):
    """Guards criterion 2 (case 2): a follow-up 3 days ago, status change included, and a future
    follow-up (0 days) are not stalled; the same deal without follow-ups is."""
    rows = _case_activities()
    await _seed(async_session_maker, _manager(), _user("ali", "Ali"), *rows)
    snapshot = await _snapshot(async_session_maker)
    assert _stalled_ids(_member(snapshot, "Ali")["stalled_deals"]["lead"], rows) == {"d2d"}


@pytest.mark.asyncio
async def test_contact_messages_reset_idle_days(async_session_maker):
    """Guards criterion 2 (case 3): inbound or outbound messages of the deal's contact count;
    other contacts' messages and sibling deals' follow-ups do not."""
    rows = _case_messages()
    await _seed(async_session_maker, _manager(), _user("ali", "Ali"), *rows)
    snapshot = await _snapshot(async_session_maker)
    assert _stalled_ids(_member(snapshot, "Ali")["stalled_deals"]["lead"], rows) == {
        "d3other",
        "d3s2",
    }


@pytest.mark.asyncio
async def test_stalled_by_status_and_team_sum(async_session_maker):
    """Guards criterion 1 (case 4): won and lost excluded, per-status count and amount,
    team equals members summed, an inactive account's stalled deal counts."""
    await _seed(
        async_session_maker,
        _manager(),
        _user("ali", "Ali"),
        _user("parked", "Parked", is_active=False),
        _deal("lead", "k1", "10.00"),
        _deal("following", "k2", "20.00", status="following"),
        _deal("fresh", "k3", "40.00", status="following", created_at=NAIVE_NOW - DAY),
        _deal("negotiating", "k4", "80.00", status="negotiating"),
        _deal("won", "k5", "100.00", status="won", won_at=LONG_AGO),
        _deal("lost", "k6", "200.00", status="lost"),
        _deal("boss-lead", "k7", "5.00", assigned_to=BOSS),
        _deal("parked-following", "k8", "3.00", assigned_to="parked", status="following"),
    )
    snapshot = await _snapshot(async_session_maker)
    assert _member(snapshot, "Ali")["stalled_deals"] == {
        "lead": {"count": 1, "amount": "10.00"},
        "following": {"count": 1, "amount": "20.00"},
        "negotiating": {"count": 1, "amount": "80.00"},
    }
    assert _member(snapshot, "Parked")["stalled_deals"]["following"] == {
        "count": 1,
        "amount": "3.00",
    }
    assert snapshot["team"]["stalled_deals"] == {
        "lead": {"count": 2, "amount": "15.00"},
        "following": {"count": 2, "amount": "23.00"},
        "negotiating": {"count": 1, "amount": "80.00"},
    }
    for status in STATUSES:
        members = [member["stalled_deals"][status] for member in snapshot["members"]]
        assert snapshot["team"]["stalled_deals"][status] == {
            "count": sum(item["count"] for item in members),
            "amount": format(sum(Decimal(item["amount"]) for item in members), ".2f"),
        }


@pytest.mark.asyncio
async def test_thirty_first_member_stalled_counts_in_team(async_session_maker):
    """Guards criterion 1 (case 4): the stalled deal of the person left out of members counts."""
    reports = [_user(f"s{index:02}", f"S{index:02}") for index in range(30)]
    wins = [
        _deal(
            f"w{index:02}",
            "kw",
            f"{(index + 1) * 10}.00",
            assigned_to=f"s{index:02}",
            status="won",
            won_at=NAIVE_NOW,
        )
        for index in range(30)
    ]
    await _seed(
        async_session_maker,
        _manager(),
        *reports,
        *wins,
        _deal("boss-won", "kb", "1.00", assigned_to=BOSS, status="won", won_at=NAIVE_NOW),
        _deal("boss-lead", "kl", "7.00", assigned_to=BOSS),
    )
    snapshot = await _snapshot(async_session_maker)
    assert snapshot["truncated"] is True
    assert "Boss" not in {member["name"] for member in snapshot["members"]}
    assert snapshot["team"]["stalled_deals"]["lead"] == {"count": 1, "amount": "7.00"}


@pytest.mark.asyncio
async def test_batch_matches_build_deal_snapshot(async_session_maker):
    """Guards criteria 2 and 3 (case 5): per open deal, the batch verdict equals the stalled flag
    build_deal_snapshot computes, over all situations of cases 1 to 3."""
    rows = [*_case_created_only(), *_case_activities(), *_case_messages()]
    await _seed(async_session_maker, _manager(), _user("ali", "Ali"), *rows)
    snapshot = await _snapshot(async_session_maker)
    batch = _stalled_ids(_member(snapshot, "Ali")["stalled_deals"]["lead"], rows)

    single = set()
    async with async_session_maker() as session:
        manager = await session.get(User, BOSS)
        for deal_id in (row.id for row in rows if isinstance(row, Deal)):
            deal = await session.get(Deal, deal_id)
            contact = await session.get(Contact, deal.contact_id)
            result = await build_deal_snapshot(
                session, user=manager, deal=deal, contact=contact, now=NOW
            )
            if result["deal"]["stalled"]:
                single.add(deal_id)
    assert batch == single == {"d1a", "d2d", "d3other", "d3s2"}


async def _count_statements(maker, manager_id):
    async with maker() as session:
        manager = await session.get(User, manager_id)
        engine = session.bind.sync_engine
        statements = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            await build_team_snapshot(session, manager=manager, now=NOW)
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        return len(statements)


def _busy_team(manager_id, member_id, size):
    rows = [_manager(manager_id, manager_id), _user(member_id, member_id, manager_id=manager_id)]
    for index in range(size):
        key = f"{member_id}-{index}"
        rows += [
            _contact(f"c-{key}"),
            _deal(f"d-{key}", f"c-{key}", "1.00", assigned_to=member_id),
            _activity(f"a-{key}", f"d-{key}", f"c-{key}", NAIVE_NOW - 3 * DAY),
            _message(f"m-{key}", f"c-{key}", NAIVE_NOW - 2 * DAY),
        ]
    return rows


@pytest.mark.asyncio
async def test_statement_count_does_not_grow_with_deals(async_session_maker):
    """Guards criterion 3 (case 6): 3 and 12 open deals execute the same number of statements."""
    await _seed(
        async_session_maker,
        *_busy_team("boss3", "few", 3),
        *_busy_team("boss12", "many", 12),
    )
    few = await _count_statements(async_session_maker, "boss3")
    many = await _count_statements(async_session_maker, "boss12")
    assert few > 0
    assert few == many
