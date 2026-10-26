"""CRM-TASK-036 tests for deal next-step contracts and business rules."""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.user import User
from app.services import ai_api_client, deal_next_step_service
from app.services.ai_assist_service import DEAL_CLOSED
from app.services.ai_features import (
    DEAL_NEXT_STEP_FEATURE,
    DEAL_NEXT_STEP_SCHEMA,
    DEAL_NEXT_STEP_SYSTEM,
    MAX_TOKENS,
    validate_deal_next_step,
)
from app.services.deal_next_step_service import build_deal_snapshot, generate_deal_next_step


def _user():
    return User(
        id="user", name="User", email="user@example.test", password_hash="hash", role="sales"
    )


def _deal(**values):
    return Deal(id="deal", contact_id="contact", title="Deal", assigned_to="user", **values)


def _contact(**values):
    return Contact(id="contact", name="Contact", company="Company", assigned_to="user", **values)


def _activity(kind="phone", record_id="activity"):
    return Activity(id=record_id, contact_id="contact", deal_id="deal", user_id="user", type=kind)


def _message():
    return Message(
        id="message",
        contact_id="contact",
        channel="whatsapp",
        direction="inbound",
        sender_id="sender",
        recipient_id="recipient",
        body="Hello",
    )


async def _seed(maker, *records):
    async with maker() as session:
        session.add_all(records)
        await session.commit()


def _configured(monkeypatch, enabled=True):
    monkeypatch.setattr(deal_next_step_service.ai_api_client, "is_ai_configured", lambda: enabled)


def _client(monkeypatch, result, calls):
    async def send_message(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(deal_next_step_service.ai_api_client, "send_message", send_message)


@pytest.mark.parametrize(
    ("output", "stalled", "expected"),
    [
        ({"assessment": "a", "stall_reason": "", "next_steps": ["n"]}, False, True),
        ({"assessment": "a" * 400, "stall_reason": "", "next_steps": ["n"]}, False, True),
        ({"assessment": " ", "stall_reason": "", "next_steps": ["n"]}, False, False),
        ({"assessment": "a" * 401, "stall_reason": "", "next_steps": ["n"]}, False, False),
        ({"assessment": "a", "stall_reason": "r", "next_steps": ["n"]}, True, True),
        ({"assessment": "a", "stall_reason": "r" * 300, "next_steps": ["n"]}, True, True),
        ({"assessment": "a", "stall_reason": "", "next_steps": ["n"]}, True, False),
        ({"assessment": "a", "stall_reason": "r" * 301, "next_steps": ["n"]}, True, False),
        ({"assessment": "a", "stall_reason": "   ", "next_steps": ["n"]}, False, True),
        ({"assessment": "a", "stall_reason": "r", "next_steps": ["n"]}, False, False),
        ({"assessment": "a", "stall_reason": "", "next_steps": ["n", "n", "n"]}, False, True),
        ({"assessment": "a", "stall_reason": "", "next_steps": []}, False, False),
        ({"assessment": "a", "stall_reason": "", "next_steps": ["n"] * 4}, False, False),
        ({"assessment": "a", "stall_reason": "", "next_steps": ["n" * 200]}, False, True),
        ({"assessment": "a", "stall_reason": "", "next_steps": ["n" * 201]}, False, False),
        ({"assessment": "a", "stall_reason": "", "next_steps": [" "]}, False, False),
        ({"assessment": "a", "stall_reason": "", "next_steps": [1]}, False, False),
        ({"assessment": "a", "stall_reason": ""}, False, False),
        ({"assessment": "a", "stall_reason": "", "next_steps": ["n"], "extra": 1}, False, False),
        ([], False, False),
    ],
)
def test_validate_deal_next_step_boundaries_and_shape(output, stalled, expected):
    """Guards criterion 1: exact shape, code-point bounds, and stalled-dependent output."""
    assert (validate_deal_next_step(output, stalled) is not None) is expected


def test_next_step_system_states_one_sentence_150_character_limit():
    """Guards CRM-TASK-073 criterion 2: system prompt asks for one sentence of at most 150 characters."""
    assert "each one sentence of at most 150 characters" in DEAL_NEXT_STEP_SYSTEM
    assert "each 1-200 characters" not in DEAL_NEXT_STEP_SYSTEM
    assert "1-120" not in DEAL_NEXT_STEP_SYSTEM


def test_validate_deal_next_step_strips_text_before_returning():
    """Guards criterion 1: accepted fields are normalized without truncation."""
    assert validate_deal_next_step(
        {"assessment": " a ", "stall_reason": " ", "next_steps": [" n "]}, False
    ) == {"assessment": "a", "stall_reason": "", "next_steps": ["n"]}


@pytest.mark.asyncio
async def test_request_uses_next_step_contract_and_snapshot(async_session_maker, monkeypatch):
    """Guards criterion 2: client request has the fixed contract and server-built snapshot."""
    user, deal, contact = _user(), _deal(), _contact()
    await _seed(async_session_maker, user, deal, contact, _activity())
    _configured(monkeypatch)
    calls = []
    _client(
        monkeypatch,
        ai_api_client.AiApiResult(
            "ok", {"assessment": "a", "stall_reason": "", "next_steps": ["n"]}
        ),
        calls,
    )
    async with async_session_maker() as session:
        deal_record = await session.get(Deal, "deal")
        contact_record = await session.get(Contact, "contact")
        expected = await build_deal_snapshot(
            session,
            user=user,
            deal=deal_record,
            contact=contact_record,
            now=datetime.now().astimezone(),
        )
        await generate_deal_next_step(session, user=user, deal_id="deal", output_language="en")
    call = calls[0]
    payload = json.loads(call["messages"][0]["content"])
    assert call["feature"] == DEAL_NEXT_STEP_FEATURE and call["max_tokens"] == MAX_TOKENS == 4000
    assert call["schema"] == DEAL_NEXT_STEP_SCHEMA and call["system"] == DEAL_NEXT_STEP_SYSTEM
    assert "untrusted data" in call["system"]
    assert set(payload) == {"as_of", "output_language", "snapshot"}
    assert payload["snapshot"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("closed_status", ["won", "lost"])
async def test_preflight_order_and_history_skip_client_and_audit(
    async_session_maker, monkeypatch, closed_status
):
    """Guards criterion 3: ordered preflight cases do not call AI or write an audit row."""
    user, deal, contact = _user(), _deal(), _contact()
    await _seed(async_session_maker, user, deal, contact)
    calls = []
    _client(
        monkeypatch,
        ai_api_client.AiApiResult(
            "ok", {"assessment": "a", "stall_reason": "", "next_steps": ["n"]}
        ),
        calls,
    )
    async with async_session_maker() as session:
        deal_record = await session.get(Deal, "deal")
        deal_record.status = closed_status
        _configured(monkeypatch, False)
        invalid = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="fr"
        )
        closed = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
        deal_record.status = "lead"
        unconfigured = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
        _configured(monkeypatch)
        no_history = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
        session.add(_activity("status change", "status-change"))
        await session.flush()
        status_only = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
        audits = (await session.execute(select(AiRequest))).scalars().all()
        assert calls == [] and audits == []
        session.add(_activity(record_id="phone"))
        await session.flush()
        phone_history = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
    assert [item.category for item in (invalid, closed, unconfigured, no_history, status_only)] == [
        "invalid_input",
        DEAL_CLOSED,
        "not_configured",
        "no_history",
        "no_history",
    ]
    assert phone_history.category == "ok" and len(calls) == 1


@pytest.mark.asyncio
async def test_message_is_history_and_missing_or_deleted_records_raise(
    async_session_maker, monkeypatch
):
    """Guards criterion 3: messages count as history and unreadable source records raise ValueError."""
    user, deal, contact = _user(), _deal(), _contact()
    await _seed(async_session_maker, user, deal, contact, _message())
    _configured(monkeypatch)
    calls = []
    _client(
        monkeypatch,
        ai_api_client.AiApiResult(
            "ok", {"assessment": "a", "stall_reason": "", "next_steps": ["n"]}
        ),
        calls,
    )
    async with async_session_maker() as session:
        assert (
            await generate_deal_next_step(session, user=user, deal_id="deal", output_language="en")
        ).category == "ok"
        with pytest.raises(ValueError):
            await generate_deal_next_step(
                session, user=user, deal_id="missing", output_language="fr"
            )
        (await session.get(Deal, "deal")).deleted_at = datetime.utcnow()
        await session.flush()
        with pytest.raises(ValueError):
            await generate_deal_next_step(session, user=user, deal_id="deal", output_language="fr")
        await session.rollback()
        (await session.get(Contact, "contact")).deleted_at = datetime.utcnow()
        await session.flush()
        with pytest.raises(ValueError):
            await generate_deal_next_step(session, user=user, deal_id="deal", output_language="fr")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        {"assessment": "", "stall_reason": "", "next_steps": ["n"]},
        {"assessment": "a", "stall_reason": "r", "next_steps": ["n"]},
        {"assessment": "a", "stall_reason": "", "next_steps": ["n"] * 4},
        {"assessment": "a", "stall_reason": "", "next_steps": ["n"], "extra": 1},
    ],
)
async def test_invalid_output_becomes_audited_error(async_session_maker, monkeypatch, output):
    """Guards criterion 4: invalid successful output is reclassified and keeps request id."""
    user, deal, contact = _user(), _deal(), _contact()
    await _seed(async_session_maker, user, deal, contact, _activity())
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult("ok", output, "request-invalid"), [])
    async with async_session_maker() as session:
        result = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == audit.outcome == "error"
    assert result.assessment is None and result.request_id == audit.request_id == "request-invalid"
    assert (audit.feature, audit.contact_id) == (DEAL_NEXT_STEP_FEATURE, "contact")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "unavailable"])
async def test_client_failure_is_audited(async_session_maker, monkeypatch, outcome):
    """Guards criterion 4: AI error and temporary unavailability retain their audit outcome."""
    user, deal, contact = _user(), _deal(), _contact()
    await _seed(async_session_maker, user, deal, contact, _activity())
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult(outcome, request_id="request-result"), [])
    async with async_session_maker() as session:
        result = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert (
        result.category == audit.outcome == outcome
        and result.request_id == audit.request_id == "request-result"
    )


@pytest.mark.asyncio
async def test_success_normalizes_output_and_returns_snapshot_facts(
    async_session_maker, monkeypatch
):
    """Guards criterion 4: successful output, audit, and four snapshot facts are returned unchanged."""
    created = datetime.utcnow() - timedelta(days=8)
    user, deal, contact = _user(), _deal(created_at=created), _contact()
    await _seed(async_session_maker, user, deal, contact, _activity())
    _configured(monkeypatch)
    _client(
        monkeypatch,
        ai_api_client.AiApiResult(
            "ok", {"assessment": " a ", "stall_reason": " ", "next_steps": [" n "]}, "request-ok"
        ),
        [],
    )
    async with async_session_maker() as session:
        deal_record = await session.get(Deal, "deal")
        contact_record = await session.get(Contact, "contact")
        snapshot = await build_deal_snapshot(
            session,
            user=user,
            deal=deal_record,
            contact=contact_record,
            now=datetime.now().astimezone(),
        )
        result = await generate_deal_next_step(
            session, user=user, deal_id="deal", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert (
        result.category,
        result.request_id,
        result.assessment,
        result.stall_reason,
        result.next_steps,
    ) == ("ok", "request-ok", "a", "", ["n"])
    assert (
        result.idle_days,
        result.days_in_status,
        result.stalled,
        result.truncated,
    ) == (
        snapshot["deal"]["idle_days"],
        snapshot["deal"]["days_in_status"],
        snapshot["deal"]["stalled"],
        snapshot["truncated"],
    )
    assert (audit.outcome, audit.request_id, audit.feature, audit.contact_id) == (
        "ok",
        "request-ok",
        DEAL_NEXT_STEP_FEATURE,
        "contact",
    )
