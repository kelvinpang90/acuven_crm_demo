"""CRM-TASK-027 acceptance coverage for customer-summary business rules."""

import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.models.ai_request import AiRequest
from app.models.autocount_document import AutocountDocument
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.task import Task
from app.models.user import User
from app.services import access_service, ai_api_client, contact_summary_service
from app.services.ai_features import SUMMARY_FEATURE, SUMMARY_SCHEMA, validate_summary

NOW = datetime(2026, 10, 15, 8, 0)


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


def _user(user_id, role="sales", **values):
    return User(
        id=user_id,
        name=user_id,
        email=f"{user_id}@test.invalid",
        password_hash="x",
        role=role,
        **values,
    )


def _contact(**values):
    return Contact(id="contact", name="Name", created_at=NOW, **values)


def _message(message_id, at, **values):
    fields = {
        "id": message_id,
        "contact_id": "contact",
        "channel": "whatsapp",
        "direction": "inbound",
        "sender_id": "from",
        "recipient_id": "to",
        "body": "body",
        "created_at": at,
    }
    fields.update(values)
    return Message(**fields)


@pytest.mark.asyncio
async def test_owner_scope_matches_all_three_roles_and_hides_unassigned(async_session_maker):
    """Guards the acceptance criterion that scope exactly matches owner access rules."""
    await _seed(
        async_session_maker,
        _user("admin", "admin"),
        _user("manager", "manager"),
        _user("staff", manager_id="manager"),
        _user("other"),
        *[
            Deal(id=f"scope-{owner}", contact_id="contact", assigned_to=owner)
            for owner in ("admin", "manager", "staff", "other")
        ],
        Deal(id="scope-unassigned", contact_id="contact", assigned_to=None),
    )
    async with async_session_maker() as session:
        scopes = [
            await access_service.owner_scope(session, user, Deal.assigned_to)
            for user in [await session.get(User, key) for key in ("admin", "manager", "staff")]
        ]
        rows = []
        for scope in scopes:
            rows.append(
                (await session.execute(select(Deal.id).where(*scope).order_by(Deal.id)))
                .scalars()
                .all()
            )
    assert scopes[0] == []
    assert rows == [
        ["scope-admin", "scope-manager", "scope-other", "scope-staff", "scope-unassigned"],
        ["scope-manager", "scope-staff"],
        ["scope-staff"],
    ]


@pytest.mark.asyncio
async def test_snapshot_fields_order_limits_visibility_and_truncation(async_session_maker):
    """Guards every snapshot block's fields, ordering, caps, and sales visibility criteria."""
    rows = [
        _user("sales"),
        _user("other"),
        _contact(tags=["t" * 31] * 11, notes="n" * 501, last_contact=NOW.date()),
    ]
    rows += [
        Deal(
            id=f"d{index:02}",
            contact_id="contact",
            assigned_to="sales" if index != 10 else "other",
            title="d" * 101,
            status="won" if index == 0 else "lost",
            priority="high",
            amount=Decimal("12.5"),
            won_at=NOW,
            updated_at=NOW + timedelta(seconds=index),
        )
        for index in range(11)
    ]
    rows += [
        Task(
            id=f"t{index}",
            contact_id="contact",
            assigned_to="sales",
            title="x" * 101,
            priority="low",
            due_date=None if index == 5 else NOW.date() + timedelta(days=index),
            is_done=False,
        )
        for index in range(6)
    ]
    rows += [
        AutocountDocument(
            id=f"a{index}",
            doc_type="invoice" if index == 0 else "quotation",
            doc_no=f"no{index}",
            customer_code="code",
            contact_id="contact",
            status="open",
            total_amount=Decimal("2"),
            currency_code="MYR",
            doc_date=NOW.date() - timedelta(days=index),
            outstanding_amount=Decimal("1"),
            due_date=NOW.date(),
            validity="v" * 101,
        )
        for index in range(6)
    ]
    rows += [
        Activity(
            id=f"a{index}",
            contact_id="contact",
            user_id="other",
            deal_id="d00",
            type="phone",
            content="c" * 301,
            follow_date=NOW + timedelta(seconds=index),
        )
        for index in range(16)
    ]
    rows += [
        _message(
            f"m{index:02}",
            NOW + timedelta(seconds=index),
            channel="email" if index == 10 else "whatsapp",
            subject="s" * 101,
            body="b" * 301,
            author_kind=None,
        )
        for index in range(11)
    ]
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await contact_summary_service.build_contact_summary_snapshot(
            session, user=await session.get(User, "sales"), contact_id="contact"
        )
    assert len(snapshot["deals"]) == 10
    assert len(snapshot["open_tasks"]) == len(snapshot["documents"]) == 5
    assert len(snapshot["activities"]) == 15 and len(snapshot["messages"]) == 10
    assert snapshot["deals"][0]["title"] == "d" * 100 and snapshot["deals"][0]["amount"] == "12.50"
    assert snapshot["deals"][0]["updated_at"] > snapshot["deals"][-1]["updated_at"]
    assert snapshot["open_tasks"][-1]["due_date"] == (NOW.date() + timedelta(days=4)).isoformat()
    assert (
        snapshot["documents"][0]["type"] == "invoice" and "outstanding" in snapshot["documents"][0]
    )
    assert snapshot["activities"][0]["at"] < snapshot["activities"][-1]["at"]
    assert (
        snapshot["messages"][0]["author_kind"] == "customer"
        and snapshot["messages"][-1]["subject"] == "s" * 100
    )
    assert snapshot["truncated"] is True
    serialized = json.dumps(snapshot, ensure_ascii=False)
    for forbidden in ("contact_id", "assigned_to", "doc_no", "customer_code", "user_id"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_snapshot_tasks_sort_same_due_date_by_id_and_undated_last(async_session_maker):
    """Guards task due-date ordering, same-date ID ordering, and undated-last criteria."""
    await _seed(
        async_session_maker,
        _user("sales"),
        _contact(),
        Task(
            id="task-b",
            contact_id="contact",
            assigned_to="sales",
            title="same-date-second",
            due_date=NOW.date(),
            is_done=False,
        ),
        Task(
            id="task-a",
            contact_id="contact",
            assigned_to="sales",
            title="same-date-first",
            due_date=NOW.date(),
            is_done=False,
        ),
        Task(
            id="task-later",
            contact_id="contact",
            assigned_to="sales",
            title="later",
            due_date=NOW.date() + timedelta(days=1),
            is_done=False,
        ),
        Task(
            id="task-undated",
            contact_id="contact",
            assigned_to="sales",
            title="undated",
            due_date=None,
            is_done=False,
        ),
    )
    async with async_session_maker() as session:
        snapshot = await contact_summary_service.build_contact_summary_snapshot(
            session, user=await session.get(User, "sales"), contact_id="contact"
        )
    assert [item["title"] for item in snapshot["open_tasks"]] == [
        "same-date-first",
        "same-date-second",
        "later",
        "undated",
    ]
    assert snapshot["open_tasks"][-1]["due_date"] is None


@pytest.mark.asyncio
async def test_snapshot_respects_deal_and_task_owner_scopes(async_session_maker):
    """Guards sales, manager, and admin deal/task visibility acceptance criteria."""
    owners = ("manager", "staff", "other", None)
    await _seed(
        async_session_maker,
        _user("admin", "admin"),
        _user("manager", "manager"),
        _user("staff", manager_id="manager"),
        _user("other"),
        _contact(),
        *[
            Deal(
                id=f"deal-{owner or 'unassigned'}",
                contact_id="contact",
                assigned_to=owner,
                title=owner or "unassigned",
                updated_at=NOW,
            )
            for owner in owners
        ],
        *[
            Task(
                id=f"task-{owner or 'unassigned'}",
                contact_id="contact",
                assigned_to=owner,
                title=owner or "unassigned",
                due_date=NOW.date(),
                is_done=False,
            )
            for owner in owners
        ],
    )
    async with async_session_maker() as session:
        snapshots = {
            role: await contact_summary_service.build_contact_summary_snapshot(
                session, user=await session.get(User, role), contact_id="contact"
            )
            for role in ("staff", "manager", "admin")
        }
    visible = {
        role: {
            block: {item["title"] for item in snapshot[block]} for block in ("deals", "open_tasks")
        }
        for role, snapshot in snapshots.items()
    }
    assert visible["staff"] == {"deals": {"staff"}, "open_tasks": {"staff"}}
    assert visible["manager"] == {
        "deals": {"manager", "staff"},
        "open_tasks": {"manager", "staff"},
    }
    assert visible["admin"] == {
        "deals": {"manager", "staff", "other", "unassigned"},
        "open_tasks": {"manager", "staff", "other", "unassigned"},
    }


@pytest.mark.asyncio
async def test_snapshot_16_kib_drops_to_newest_message_and_activity(async_session_maker):
    """Guards 16 KiB discard order, both one-record floors, and no-error overflow behavior."""
    rows = [_user("sales"), _contact(notes="\x00" * 500, tags=["\x00" * 30] * 10)]
    rows += [
        Deal(
            id=f"d{index}",
            contact_id="contact",
            assigned_to="sales",
            title="\x00" * 100,
            updated_at=NOW,
        )
        for index in range(10)
    ]
    rows += [
        Task(
            id=f"t{index}",
            contact_id="contact",
            assigned_to="sales",
            title="\x00" * 100,
            due_date=NOW.date(),
            is_done=False,
        )
        for index in range(5)
    ]
    rows += [
        AutocountDocument(
            id=f"q{index}",
            doc_type="quotation",
            doc_no=f"q{index}",
            customer_code="code",
            contact_id="contact",
            validity="\x00" * 100,
        )
        for index in range(5)
    ]
    rows += [
        Activity(
            id=f"a{index}",
            contact_id="contact",
            user_id="sales",
            deal_id="d",
            type="phone",
            content="\x00" * 300,
            follow_date=NOW + timedelta(seconds=index),
        )
        for index in range(2)
    ]
    rows += [
        _message(f"m{index:02}", NOW + timedelta(seconds=index), body="\x00" * 300)
        for index in range(2)
    ]
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await contact_summary_service.build_contact_summary_snapshot(
            session, user=await session.get(User, "sales"), contact_id="contact"
        )
    assert snapshot["truncated"] is True
    assert [item["at"] for item in snapshot["messages"]] == ["2026-10-15T08:00:01Z"]
    assert [item["at"] for item in snapshot["activities"]] == ["2026-10-15T08:00:01Z"]


@pytest.mark.asyncio
async def test_snapshot_over_16_kib_without_discard_is_not_truncated(async_session_maker):
    """Guards that size alone does not set truncated when no record or field is cut."""
    rows = [_user("sales"), _contact(notes="\x00" * 500, tags=["\x00" * 30] * 10)]
    rows += [
        Deal(
            id=f"d{index}",
            contact_id="contact",
            assigned_to="sales",
            title="\x00" * 100,
            updated_at=NOW,
        )
        for index in range(10)
    ]
    rows += [
        Task(
            id=f"t{index}",
            contact_id="contact",
            assigned_to="sales",
            title="\x00" * 100,
            due_date=NOW.date(),
            is_done=False,
        )
        for index in range(5)
    ]
    rows += [
        AutocountDocument(
            id=f"q{index}",
            doc_type="quotation",
            doc_no=f"q{index}",
            customer_code="code",
            contact_id="contact",
            validity="\x00" * 100,
        )
        for index in range(5)
    ]
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await contact_summary_service.build_contact_summary_snapshot(
            session, user=await session.get(User, "sales"), contact_id="contact"
        )
    assert (
        len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        > 16 * 1024
    )
    assert snapshot["truncated"] is False


@pytest.mark.parametrize(
    "output",
    [
        {"summary": "", "suggested_next_step": ""},
        {"summary": "x" * 601, "suggested_next_step": ""},
        {"summary": "ok", "suggested_next_step": "x" * 241},
        {"summary": "ok"},
        {"summary": "ok", "suggested_next_step": "", "extra": "x"},
    ],
)
def test_summary_validator_rejects_every_invalid_field_boundary(output):
    """Guards the summary/output length and exact-shape acceptance criteria."""
    assert validate_summary(output) is None


@pytest.mark.asyncio
async def test_generation_invalid_configuration_and_no_history_do_not_call_or_audit(
    async_session_maker, monkeypatch
):
    """Guards the required parameter, configuration, and empty-history check order."""
    await _seed(async_session_maker, _user("sales"), _contact())
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        return ai_api_client.AiApiResult("ok")

    monkeypatch.setattr(contact_summary_service.ai_api_client, "send_message", send_message)
    monkeypatch.setattr(contact_summary_service.ai_api_client, "is_ai_configured", lambda: False)
    async with async_session_maker() as session:
        user = await session.get(User, "sales")
        invalid = await contact_summary_service.generate_contact_summary(
            session, user=user, contact_id="contact", output_language="bad"
        )
        unconfigured = await contact_summary_service.generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        monkeypatch.setattr(contact_summary_service.ai_api_client, "is_ai_configured", lambda: True)
        no_history = await contact_summary_service.generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audits = (await session.execute(select(AiRequest))).scalars().all()
    assert [item.category for item in (invalid, unconfigured, no_history)] == [
        "invalid_input",
        "not_configured",
        "no_history",
    ]
    assert calls == audits == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_output",
    [
        {"summary": "x" * 601, "suggested_next_step": ""},
        {"summary": "ok", "suggested_next_step": "x" * 241},
        {"summary": "ok"},
        {"summary": "ok", "suggested_next_step": "", "extra": "x"},
    ],
)
async def test_generation_order_contract_results_and_audit(
    async_session_maker, monkeypatch, invalid_output
):
    """Guards every invalid output's error audit, request id, and unavailable auditing criteria."""
    await _seed(async_session_maker, _user("sales"), _contact(), _message("m", NOW))
    calls = []
    monkeypatch.setattr(contact_summary_service.ai_api_client, "is_ai_configured", lambda: True)
    results = iter(
        [
            ai_api_client.AiApiResult(
                "ok", {"summary": " summary ", "suggested_next_step": " "}, "good"
            ),
            ai_api_client.AiApiResult("ok", invalid_output, "bad"),
            ai_api_client.AiApiResult("unavailable", request_id="down"),
        ]
    )

    async def send_message(**kwargs):
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(contact_summary_service.ai_api_client, "send_message", send_message)
    async with async_session_maker() as session:
        user = await session.get(User, "sales")
        invalid = await contact_summary_service.generate_contact_summary(
            session, user=user, contact_id="contact", output_language="bad"
        )
        good = await contact_summary_service.generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        bad = await contact_summary_service.generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        unavailable = await contact_summary_service.generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audits = (
            (await session.execute(select(AiRequest).order_by(AiRequest.request_id)))
            .scalars()
            .all()
        )
    assert (
        invalid.category == "invalid_input"
        and good.summary == "summary"
        and good.suggested_next_step == ""
    )
    assert (bad.category, bad.request_id, unavailable.category) == ("error", "bad", "unavailable")
    assert [(audit.feature, audit.outcome, audit.request_id) for audit in audits] == [
        (SUMMARY_FEATURE, "error", "bad"),
        (SUMMARY_FEATURE, "unavailable", "down"),
        (SUMMARY_FEATURE, "ok", "good"),
    ]
    assert all(
        call["feature"] == SUMMARY_FEATURE
        and call["max_tokens"] == 4000
        and call["schema"] == SUMMARY_SCHEMA
        for call in calls
    )
    assert "untrusted data" in calls[0]["system"]
