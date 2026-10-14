"""CRM-TASK-022 tests for AI conversation snapshots and audit rows."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    SNAPSHOT_LIMIT_BYTES,
    build_conversation_snapshot,
    write_ai_request,
)

NOW = datetime(2026, 10, 14, 8, 0, 0)


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


def _contact(**values):
    return Contact(id="contact-1", name="Name", **values)


def _message(message_id, created_at, **values):
    fields = {
        "id": message_id,
        "contact_id": "contact-1",
        "channel": "whatsapp",
        "direction": "inbound",
        "sender_id": "sender",
        "recipient_id": "recipient",
        "body": "body",
        "created_at": created_at,
    }
    fields.update(values)
    return Message(**fields)


@pytest.mark.asyncio
async def test_snapshot_fields_truncation_inference_and_delivery_follow_acceptance_criteria(
    async_session_maker,
):
    """Guards snapshot fields, field caps, handover state, author inference, and delivery values."""
    await _seed(
        async_session_maker,
        _contact(
            company="Company",
            industry="Industry",
            tags=["t" * 31] * 11,
            notes="n" * 501,
            wa_handover_active=True,
            wa_handover_expires_at=NOW + timedelta(minutes=1),
        ),
        Deal(
            id="deal-1",
            contact_id="contact-1",
            title="d" * 101,
            status="lead",
            amount=Decimal("12.50"),
            updated_at=NOW,
        ),
        _message(
            "old",
            NOW - timedelta(seconds=50),
            direction="outbound",
            author_kind="agent",
            external_id=None,
        ),
        _message(
            "new",
            NOW - timedelta(seconds=10),
            direction="outbound",
            author_kind="agent",
            external_id=None,
        ),
        _message(
            "stored", NOW, direction="outbound", author_kind="console", delivery_status="read"
        ),
        _message(
            "email",
            NOW + timedelta(seconds=1),
            channel="email",
            direction="outbound",
            subject="s" * 201,
            body="b" * 2001,
            author_kind=None,
        ),
    )
    async with async_session_maker() as session:
        snapshot = await build_conversation_snapshot(session, "contact-1", now=NOW)

    assert snapshot["contact"] == {
        "name": "Name",
        "company": "Company",
        "industry": "Industry",
        "tags": ["t" * 30] * 10,
        "notes": "n" * 500,
    }
    assert snapshot["open_deals"] == [{"title": "d" * 100, "status": "lead", "amount": "12.50"}]
    assert snapshot["handover"] == {"active": True, "expires_at": "2026-10-14T08:01:00Z"}
    assert [message.get("delivery") for message in snapshot["messages"][:3]] == [
        "unconfirmed",
        "sending",
        "read",
    ]
    assert snapshot["messages"][-1]["author_kind"] == "agent"
    assert snapshot["messages"][-1]["subject"] == "s" * 200
    assert snapshot["truncated"] is True
    assert "id" not in compact_json(snapshot) and "phone" not in compact_json(snapshot)


@pytest.mark.asyncio
async def test_snapshot_uses_recent_40_and_drops_oldest_until_16_kib(async_session_maker):
    """Guards the recent-40 selection, chronological ordering, and 16 KiB oldest-first discard criterion."""
    rows = [_contact()]
    for index in range(41):
        rows.append(_message(f"{index:02}", NOW + timedelta(seconds=index), body="x" * 1000))
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await build_conversation_snapshot(session, "contact-1", now=NOW)

    assert snapshot["messages"][-1]["body"] == "x" * 1000
    assert len(snapshot["messages"]) < 40
    assert snapshot["truncated"] is True
    assert len(compact_json(snapshot).encode("utf-8")) <= SNAPSHOT_LIMIT_BYTES


@pytest.mark.asyncio
async def test_snapshot_selects_the_recent_40_in_chronological_id_order(async_session_maker):
    """Guards the criterion that exactly the recent 40 messages are ordered by time then id."""
    rows = [_contact()]
    for index in range(42):
        rows.append(
            _message(
                f"message-{index:02}",
                NOW + timedelta(seconds=index // 2),
                body=f"body-{index:02}",
            )
        )
    await _seed(async_session_maker, *rows)
    async with async_session_maker() as session:
        snapshot = await build_conversation_snapshot(session, "contact-1", now=NOW)

    assert [message["body"] for message in snapshot["messages"]] == [
        f"body-{index:02}" for index in range(2, 42)
    ]
    assert snapshot["truncated"] is False


@pytest.mark.asyncio
async def test_snapshot_keeps_latest_message_when_it_alone_exceeds_16_kib(async_session_maker):
    """Guards the criterion that an oversized final message is retained without setting truncation by size alone."""
    controls = "\x00"
    await _seed(
        async_session_maker,
        _contact(notes=controls * 500, tags=[controls * 30] * 10),
        *[
            Deal(
                id=f"deal-{index}",
                contact_id="contact-1",
                title=controls * 100,
                status="lead",
                amount=Decimal("1"),
                updated_at=NOW,
            )
            for index in range(5)
        ],
        _message("latest", NOW, body=controls * 2000),
    )
    async with async_session_maker() as session:
        snapshot = await build_conversation_snapshot(session, "contact-1", now=NOW)

    assert [message["body"] for message in snapshot["messages"]] == [controls * 2000]
    assert len(compact_json(snapshot).encode("utf-8")) > SNAPSHOT_LIMIT_BYTES
    assert snapshot["truncated"] is False


@pytest.mark.asyncio
async def test_audit_writer_stores_only_the_required_audit_fields(async_session_maker):
    """Guards the criterion that the audit writer inserts one minimal ai_requests row."""
    async with async_session_maker() as session:
        record = await write_ai_request(
            session,
            user_id="user-1",
            contact_id="contact-1",
            feature="crm.translate",
            request_id="request-1",
            outcome="ok",
            created_at=NOW,
        )
        await session.commit()
        stored = (await session.execute(select(AiRequest))).scalar_one()

    assert stored.id == record.id
    assert (
        stored.user_id,
        stored.contact_id,
        stored.feature,
        stored.request_id,
        stored.outcome,
        stored.created_at,
    ) == (
        "user-1",
        "contact-1",
        "crm.translate",
        "request-1",
        "ok",
        NOW,
    )
