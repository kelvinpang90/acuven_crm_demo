"""Assignee suggestion business layer (CRM-TASK-087, docs/contact-assign-suggest.md).

Cast: Boss manages Rep One and Rep Two; Outsider is a rep on nobody's team;
Gone is an inactive rep on Boss's team, created before everybody else so an
inactive-user bug would show up in the fallback. Rep One is the earliest
active sales, so the fallback lands on them.
"""

from datetime import datetime, timedelta
from typing import Optional

import pytest
from sqlalchemy import select

from app.models.contact import Contact
from app.models.deal import Deal
from app.models.routing_rule import RoutingRule
from app.models.user import User
from app.services import access_service, routing_service
from tests._people import logged_in_as

T0 = datetime(2026, 1, 1)

ADMIN = User(id="u-admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
BOSS = User(id="u-boss", name="Boss", email="boss@example.com", password_hash="x", role="manager")
REP1 = User(
    id="u-rep1",
    name="Rep One",
    email="rep1@example.com",
    password_hash="x",
    role="sales",
    manager_id="u-boss",
)
GONE = User(
    id="u-gone",
    name="Gone",
    email="gone@example.com",
    password_hash="x",
    role="sales",
    manager_id="u-boss",
    is_active=False,
)


def _cast(*, with_active_sales: bool = True) -> list[User]:
    def user(uid, name, role, minutes, manager_id=None, is_active=True):
        return User(
            id=uid,
            name=name,
            email=f"{uid}@example.com",
            password_hash="x",
            role=role,
            manager_id=manager_id,
            is_active=is_active,
            created_at=T0 + timedelta(minutes=minutes),
        )

    people = [
        user("u-gone", "Gone", "sales", -10, manager_id="u-boss", is_active=False),
        user("u-admin", "Admin", "admin", 0),
        user("u-boss", "Boss", "manager", 1),
    ]
    if with_active_sales:
        people += [
            user("u-rep1", "Rep One", "sales", 2, manager_id="u-boss"),
            user("u-rep2", "Rep Two", "sales", 3, manager_id="u-boss"),
            user("u-out", "Outsider", "sales", 4),
        ]
    return people


def _rule(name, strategy, targets, priority=0, conditions=None) -> RoutingRule:
    return RoutingRule(
        id=f"r-{name}",
        name=name,
        strategy=strategy,
        target_users=targets,
        priority=priority,
        conditions=conditions,
        is_active=True,
    )


def _contacts(owner: str, n: int, prefix: str) -> list[Contact]:
    return [Contact(id=f"{prefix}-{i}", name=f"{prefix} {i}", assigned_to=owner) for i in range(n)]


async def _seed(maker, rows, *, with_active_sales: bool = True) -> None:
    async with maker() as session:
        session.add_all(_cast(with_active_sales=with_active_sales))
        await session.flush()
        session.add_all(rows)
        await session.commit()


async def _suggest(maker, user, **fields) -> dict:
    args = {"name": None, "company": None, "industry": None, "address": None}
    args.update(fields)
    async with maker() as session:
        return await routing_service.suggest_assignee(session, user, **args)


# ---------------------------------------------------------------------------
# 1 Candidate scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user, expected",
    [
        (ADMIN, ["u-admin", "u-boss", "u-out", "u-rep1", "u-rep2"]),
        (BOSS, ["u-boss", "u-rep1", "u-rep2"]),
        (REP1, ["u-rep1"]),
        (GONE, []),
    ],
    ids=["admin", "manager", "sales", "inactive"],
)
async def test_candidate_scope_matches_list_users(async_session_maker, client, user, expected):
    """AC: assignable_users gives admins every active user, managers themselves
    and their active reports, anyone else only themselves while active, ordered
    by name; list_users returns the same people in the same order; the
    suggestion's candidates are exactly these people."""
    await _seed(async_session_maker, [])

    async with async_session_maker() as session:
        users = await access_service.assignable_users(session, user)
    assert [u.id for u in users] == expected
    assert [u.name for u in users] == sorted(u.name for u in users)

    with logged_in_as(user):
        resp = await client.get("/api/users")
    assert resp.status_code == 200
    assert [u["id"] for u in resp.json()["data"]] == expected

    result = await _suggest(async_session_maker, user)
    assert sorted(c["user_id"] for c in result["candidates"]) == sorted(expected)


# ---------------------------------------------------------------------------
# 2 Rule dry-run
# ---------------------------------------------------------------------------


def _workload_rows():
    return [_rule("Even load", "workload", ["u-rep1", "u-rep2"]), *_contacts("u-rep1", 2, "busy")]


def _region_rows():
    return [_rule("Penang team", "region", ["u-rep2"], conditions={"keywords": ["Penang"]})]


@pytest.mark.parametrize(
    "rows, with_sales, user, fields, expected",
    [
        (
            _workload_rows,
            True,
            ADMIN,
            {"name": "Acme"},
            {
                "user_id": "u-rep2",
                "user_name": "Rep Two",
                "rule_name": "Even load",
                "fallback": False,
                "selectable": True,
            },
        ),
        (
            _region_rows,
            True,
            ADMIN,
            {"name": "Acme", "company": "  ", "address": "  Lot 5, Jalan Air Itam, Penang  "},
            {
                "user_id": "u-rep2",
                "user_name": "Rep Two",
                "rule_name": "Penang team",
                "fallback": False,
                "selectable": True,
            },
        ),
        (
            _region_rows,
            True,
            ADMIN,
            {"name": "Acme", "address": "Kuala Lumpur"},
            {
                "user_id": "u-rep1",
                "user_name": "Rep One",
                "rule_name": None,
                "fallback": True,
                "selectable": True,
            },
        ),
        (
            lambda: [_rule("Gone only", "workload", ["u-gone"])],
            False,
            ADMIN,
            {"name": "Acme"},
            {
                "user_id": None,
                "user_name": None,
                "rule_name": None,
                "fallback": True,
                "selectable": False,
            },
        ),
        (
            lambda: [_rule("Outsiders", "workload", ["u-out"])],
            True,
            BOSS,
            {"name": "Acme"},
            {
                "user_id": "u-out",
                "user_name": "Outsider",
                "rule_name": "Outsiders",
                "fallback": False,
                "selectable": False,
            },
        ),
    ],
    ids=[
        "workload-hit",
        "region-address-hit",
        "no-hit-fallback",
        "no-active-sales",
        "outside-team",
    ],
)
async def test_rule_pick(async_session_maker, rows, with_sales, user, fields, expected):
    """AC: rule_pick runs the rule logic on the trimmed form fields — a hit gives
    the user, their name and the rule's name with fallback false; no hit gives
    assign_contact's fallback with rule_name None and fallback true; no active
    sales gives all None with fallback true; selectable is whether the user is
    among the caller's candidates."""
    await _seed(async_session_maker, rows(), with_active_sales=with_sales)

    result = await _suggest(async_session_maker, user, **fields)

    assert result["rule_pick"] == expected


# ---------------------------------------------------------------------------
# 3 assign_contact regression
# ---------------------------------------------------------------------------


async def _assign_contact_before_split(db, contact: Contact) -> Optional[str]:
    """assign_contact as it was before _pick_by_rules was split out."""
    rules = await db.execute(
        select(RoutingRule)
        .where(RoutingRule.is_active == True)
        .order_by(RoutingRule.priority.asc())
    )
    for rule in rules.scalars().all():
        target_ids = rule.target_users or []
        if not target_ids:
            continue
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
            user_id = await routing_service._strategy_workload(db, eligible)
        elif rule.strategy == "region":
            user_id = await routing_service._strategy_region(db, contact, rule.conditions, eligible)
        elif rule.strategy == "win_rate":
            user_id = await routing_service._strategy_win_rate(db, eligible)
        else:
            continue
        if user_id:
            return user_id

    fallback = await db.execute(
        select(User.id)
        .where(User.is_active == True, User.role == "sales")
        .order_by(User.created_at.asc())
        .limit(1)
    )
    return fallback.scalar_one_or_none()


@pytest.mark.parametrize(
    "rows, with_sales, address, expected",
    [
        (_workload_rows, True, None, "u-rep2"),
        (_region_rows, True, "Penang", "u-rep2"),
        (_region_rows, True, "Kuala Lumpur", "u-rep1"),
        (
            lambda: [
                _rule("Empty", "workload", [], priority=0),
                _rule("Gone only", "workload", ["u-gone"], priority=1),
                _rule("Outsiders", "win_rate", ["u-out"], priority=2),
            ],
            True,
            None,
            "u-out",
        ),
        (lambda: [], True, None, "u-rep1"),
        (lambda: [_rule("Gone only", "workload", ["u-gone"])], False, None, None),
    ],
    ids=["workload", "region-hit", "fallback", "skips-empty-and-inactive", "no-rules", "no-sales"],
)
async def test_assign_contact_unchanged(async_session_maker, rows, with_sales, address, expected):
    """AC: assign_contact built on _pick_by_rules returns the same user as before
    the split for the same data, fallback to the earliest active sales included."""
    await _seed(async_session_maker, rows(), with_active_sales=with_sales)
    contact = Contact(name="Acme", address=address)

    async with async_session_maker() as session:
        now = await routing_service.assign_contact(session, contact)
        before = await _assign_contact_before_split(session, contact)

    assert now == before == expected


# ---------------------------------------------------------------------------
# 4 Figures
# ---------------------------------------------------------------------------


def _figure_rows():
    now = datetime.utcnow()
    old = now - timedelta(days=91)
    return [
        Contact(id="c-retail", name="Retail", industry="Retail", assigned_to="u-rep1"),
        Contact(
            id="c-archived",
            name="Archived",
            industry="Retail",
            assigned_to="u-rep1",
            is_archived=1,
        ),
        Contact(
            id="c-deleted", name="Deleted", industry="Retail", assigned_to="u-rep1", deleted_at=now
        ),
        Contact(id="c-food", name="Food", industry="Food", assigned_to="u-rep1"),
        Deal(id="d-open", contact_id="c-retail", status="lead", assigned_to="u-rep1"),
        Deal(id="d-won", contact_id="c-retail", status="won", assigned_to="u-rep1"),
        Deal(id="d-lost", contact_id="c-food", status="lost", assigned_to="u-rep1"),
        Deal(
            id="d-deleted",
            contact_id="c-retail",
            status="negotiating",
            assigned_to="u-rep1",
            deleted_at=now,
        ),
        Deal(
            id="d-deleted-won",
            contact_id="c-retail",
            status="won",
            assigned_to="u-rep1",
            deleted_at=now,
        ),
        Deal(
            id="d-old-won",
            contact_id="c-archived",
            status="won",
            assigned_to="u-rep1",
            created_at=old,
        ),
        Deal(
            id="d-won-deleted-contact",
            contact_id="c-deleted",
            status="won",
            assigned_to="u-rep1",
        ),
        Deal(id="d-won-food", contact_id="c-food", status="won", assigned_to="u-rep1"),
    ]


async def test_figures(async_session_maker):
    """AC: contact_count skips deleted and counts archived contacts;
    open_deal_count skips deleted, won and lost deals; won_90d/total_90d skip
    deleted deals and deals created more than 90 days ago; industry counts skip
    other industries and deleted contacts/deals; people with no rows get 0;
    industry counts are None when industry is empty."""
    await _seed(async_session_maker, _figure_rows())

    by_id = {
        c["user_id"]: c
        for c in (await _suggest(async_session_maker, ADMIN, industry=" Retail "))["candidates"]
    }
    assert by_id["u-rep1"] == {
        "user_id": "u-rep1",
        "name": "Rep One",
        "contact_count": 3,  # retail, archived, food
        "open_deal_count": 1,  # d-open
        "won_90d": 3,  # d-won, d-won-deleted-contact, d-won-food
        "total_90d": 5,  # those three + d-open, d-lost
        "industry_contact_count": 2,  # retail, archived
        "industry_won_count": 2,  # d-won, d-old-won
    }
    assert by_id["u-rep2"] == {
        "user_id": "u-rep2",
        "name": "Rep Two",
        "contact_count": 0,
        "open_deal_count": 0,
        "won_90d": 0,
        "total_90d": 0,
        "industry_contact_count": 0,
        "industry_won_count": 0,
    }

    for industry in (None, "   "):
        result = await _suggest(async_session_maker, ADMIN, industry=industry)
        for c in result["candidates"]:
            assert c["industry_contact_count"] is None
            assert c["industry_won_count"] is None
        rep1 = next(c for c in result["candidates"] if c["user_id"] == "u-rep1")
        assert rep1["contact_count"] == 3


# ---------------------------------------------------------------------------
# 5 Ordering
# ---------------------------------------------------------------------------


async def test_candidates_sorted_by_contact_count_then_name(async_session_maker):
    """AC: candidates are ordered by contact_count ascending, ties by name."""
    rows = [
        *_contacts("u-boss", 2, "boss"),
        *_contacts("u-rep2", 1, "two"),
        *_contacts("u-rep1", 1, "one"),
    ]
    await _seed(async_session_maker, rows)

    result = await _suggest(async_session_maker, ADMIN)

    assert [(c["name"], c["contact_count"]) for c in result["candidates"]] == [
        ("Admin", 0),
        ("Outsider", 0),
        ("Rep One", 1),
        ("Rep Two", 1),
        ("Boss", 2),
    ]


# ---------------------------------------------------------------------------
# 6 Read-only
# ---------------------------------------------------------------------------


async def _snapshot(session) -> dict:
    out = {}
    for model in (Contact, Deal, RoutingRule):
        table = model.__table__
        rows = await session.execute(select(table).order_by(table.c.id))
        out[table.name] = [tuple(r) for r in rows.all()]
    return out


async def test_suggestion_writes_nothing(async_session_maker):
    """AC: suggest_assignee does not write, flush, commit or change any record —
    contacts, deals and rules are unchanged and the session holds no new objects."""
    rows = [*_region_rows(), _rule("Even load", "workload", ["u-rep1", "u-rep2"], priority=1)]
    rows += _figure_rows()
    await _seed(async_session_maker, rows)

    async with async_session_maker() as session:
        before = await _snapshot(session)

    async with async_session_maker() as session:
        result = await routing_service.suggest_assignee(
            session,
            ADMIN,
            name="Probe Sdn Bhd",
            company="Probe",
            industry="Retail",
            address="Penang",
        )
        assert result["rule_pick"]["rule_name"] == "Penang team"
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        assert await _snapshot(session) == before

    async with async_session_maker() as session:
        assert await _snapshot(session) == before
