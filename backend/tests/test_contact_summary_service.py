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
    )
    async with async_session_maker() as session:
        scopes = [
            await access_service.owner_scope(session, user, Deal.assigned_to)
            for user in [await session.get(User, key) for key in ("admin", "manager", "staff")]
        ]
        rows = []
        for scope in scopes:
            rows.append(
                (await session.execute(select(User.id).where(*scope).order_by(User.id)))
                .scalars()
                .all()
            )
    assert scopes[0] == []
    assert rows == [["admin", "manager", "other", "staff"], ["manager", "staff"], ["staff"]]


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
    assert len(snapshot["deals"]) == len(snapshot["open_tasks"]) == len(snapshot["documents"]) == 5
    assert len(snapshot["activities"]) == 15 and len(snapshot["messages"]) == 10
    assert snapshot["deals"][0]["title"] == "d" * 100 and snapshot["deals"][0]["amount"] == "12.50"
    assert snapshot["deals"][-1]["updated_at"] > snapshot["deals"][0]["updated_at"]
    assert snapshot["open_tasks"][-1]["due_date"] is None
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
async def test_snapshot_16_kib_drops_oldest_messages_then_activities(async_session_maker):
    """Guards the 16 KiB discard order and minimum one-message/activity retention criteria."""
    rows = [_user("sales"), _contact(notes="\x00" * 500)]
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
        for index in range(15)
    ]
    rows += [
        _message(f"m{index:02}", NOW + timedelta(seconds=index), body="\x00" * 300)
        for index in range(10)
    ]
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await contact_summary_service.build_contact_summary_snapshot(
            session, user=await session.get(User, "sales"), contact_id="contact"
        )
    assert (
        snapshot["truncated"] is True and snapshot["messages"][-1]["at"] == "2026-10-15T08:00:09Z"
    )
    assert snapshot["messages"] and snapshot["activities"]


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
async def test_generation_order_contract_results_and_audit(async_session_maker, monkeypatch):
    """Guards no-call order plus successful, invalid, and unavailable call auditing criteria."""
    await _seed(async_session_maker, _user("sales"), _contact(), _message("m", NOW))
    calls = []
    monkeypatch.setattr(contact_summary_service.ai_api_client, "is_ai_configured", lambda: True)
    results = iter(
        [
            ai_api_client.AiApiResult(
                "ok", {"summary": " summary ", "suggested_next_step": " "}, "good"
            ),
            ai_api_client.AiApiResult("ok", {"summary": "", "suggested_next_step": ""}, "bad"),
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
    assert [(audit.feature, audit.outcome) for audit in audits] == [
        (SUMMARY_FEATURE, "error"),
        (SUMMARY_FEATURE, "unavailable"),
        (SUMMARY_FEATURE, "ok"),
    ]
    assert all(
        call["feature"] == SUMMARY_FEATURE
        and call["max_tokens"] == 4000
        and call["schema"] == SUMMARY_SCHEMA
        for call in calls
    )
    assert "untrusted data" in calls[0]["system"]
