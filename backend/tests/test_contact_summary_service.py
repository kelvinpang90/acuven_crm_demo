"""CRM-TASK-031 tests for customer-summary contracts and business rules."""

import json
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.user import User
from app.services import ai_api_client, contact_summary_service
from app.services.ai_features import (
    MAX_TOKENS,
    SUMMARY_FEATURE,
    SUMMARY_SCHEMA,
    SUMMARY_SYSTEM,
    validate_summary,
)
from app.services.contact_summary_service import build_contact_snapshot, generate_contact_summary


def _user():
    return User(
        id="user", name="User", email="user@example.test", password_hash="hash", role="sales"
    )


async def _seed(maker, *, history=False):
    user = _user()
    async with maker() as session:
        session.add_all([user, Contact(id="contact", name="Contact", company="Company")])
        if history:
            session.add(
                Activity(
                    id="activity",
                    contact_id="contact",
                    user_id="user",
                    deal_id="deal",
                    type="phone",
                )
            )
        await session.commit()
    return user


def _configured(monkeypatch, enabled=True):
    monkeypatch.setattr(contact_summary_service.ai_api_client, "is_ai_configured", lambda: enabled)


def _client(monkeypatch, result, calls):
    async def send_message(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(contact_summary_service.ai_api_client, "send_message", send_message)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ({"summary": "s", "suggested_next_step": ""}, True),
        ({"summary": "s" * 600, "suggested_next_step": "n" * 240}, True),
        ({"summary": "😀" * 600, "suggested_next_step": ""}, True),
        ({"summary": "   ", "suggested_next_step": ""}, False),
        ({"summary": "s" * 601, "suggested_next_step": ""}, False),
        ({"summary": "s", "suggested_next_step": "n" * 241}, False),
        ({"summary": "s"}, False),
        ({"summary": "s", "suggested_next_step": "", "extra": "x"}, False),
        ({"summary": [], "suggested_next_step": ""}, False),
        ({"summary": "s", "suggested_next_step": []}, False),
        ([], False),
    ],
)
def test_validate_summary_boundaries_and_shape(output, expected):
    """Guards criterion 1: code-point bounds and exact two-string output shape."""
    assert (validate_summary(output) is not None) is expected


def test_validate_summary_strips_before_measuring():
    """Guards criterion 1: valid boundary text is returned without edge whitespace."""
    output = validate_summary({"summary": f"  {'s' * 600}  ", "suggested_next_step": " n "})
    assert output == {"summary": "s" * 600, "suggested_next_step": "n"}


@pytest.mark.asyncio
async def test_request_uses_summary_contract_and_contact_snapshot(async_session_maker, monkeypatch):
    """Guards criterion 2: request contract and the exact server-built snapshot are sent."""
    user = await _seed(async_session_maker, history=True)
    _configured(monkeypatch)
    calls = []
    _client(
        monkeypatch,
        ai_api_client.AiApiResult("ok", {"summary": "Summary", "suggested_next_step": "Next"}),
        calls,
    )
    async with async_session_maker() as session:
        expected_snapshot = await build_contact_snapshot(session, user=user, contact_id="contact")
        await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )

    call = calls[0]
    payload = json.loads(call["messages"][0]["content"])
    assert call["feature"] == SUMMARY_FEATURE and call["max_tokens"] == MAX_TOKENS == 4000
    assert call["schema"] == SUMMARY_SCHEMA
    assert SUMMARY_SCHEMA["required"] == ["summary", "suggested_next_step"]
    assert SUMMARY_SCHEMA["additionalProperties"] is False
    assert "untrusted data" in call["system"] and call["system"] == SUMMARY_SYSTEM
    assert set(payload) == {"as_of", "output_language", "snapshot"}
    assert payload["as_of"].endswith("Z")
    datetime.fromisoformat(payload["as_of"].removesuffix("Z"))
    assert payload["output_language"] == "en" and payload["snapshot"] == expected_snapshot


@pytest.mark.asyncio
async def test_preflight_order_skips_client_and_audit(async_session_maker, monkeypatch):
    """Guards criterion 3: parameter, configuration, and empty-history ordering has no side effects."""
    user = await _seed(async_session_maker)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok"), calls)
    async with async_session_maker() as session:
        _configured(monkeypatch, False)
        invalid = await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="fr"
        )
        unconfigured = await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        _configured(monkeypatch)
        no_history = await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audits = (await session.execute(select(AiRequest))).scalars().all()

    assert [item.category for item in (invalid, unconfigured, no_history)] == [
        "invalid_input",
        "not_configured",
        "no_history",
    ]
    assert calls == [] and audits == []


@pytest.mark.asyncio
async def test_history_calls_client_and_missing_contact_propagates_value_error(
    async_session_maker, monkeypatch
):
    """Guards criterion 3: one activity permits a call and a missing contact is not remapped."""
    user = await _seed(async_session_maker, history=True)
    _configured(monkeypatch)
    calls = []
    _client(
        monkeypatch,
        ai_api_client.AiApiResult("ok", {"summary": "s", "suggested_next_step": ""}),
        calls,
    )
    async with async_session_maker() as session:
        await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        with pytest.raises(ValueError, match="contact not found"):
            await generate_contact_summary(
                session, user=user, contact_id="missing", output_language="en"
            )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        {"summary": "", "suggested_next_step": ""},
        {"summary": "s" * 601, "suggested_next_step": ""},
        {"summary": "s", "suggested_next_step": "n" * 241},
        {"summary": "s"},
        {"summary": "s", "suggested_next_step": "", "extra": "x"},
    ],
)
async def test_invalid_success_output_becomes_audited_error(
    async_session_maker, monkeypatch, output
):
    """Guards criterion 4: every listed invalid success is reclassified while keeping request id."""
    user = await _seed(async_session_maker, history=True)
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", output, "request-invalid"), calls)
    async with async_session_maker() as session:
        result = await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == audit.outcome == "error"
    assert result.summary is None and result.request_id == audit.request_id == "request-invalid"
    assert audit.feature == SUMMARY_FEATURE and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "unavailable"])
async def test_client_non_ok_result_is_audited(async_session_maker, monkeypatch, outcome):
    """Guards criterion 4: client error and temporary unavailability preserve their audited category."""
    user = await _seed(async_session_maker, history=True)
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult(outcome, request_id="request-result"), calls)
    async with async_session_maker() as session:
        result = await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == audit.outcome == outcome
    assert result.request_id == audit.request_id == "request-result"
    assert audit.feature == SUMMARY_FEATURE and len(calls) == 1


@pytest.mark.asyncio
async def test_success_normalizes_output_and_audits(async_session_maker, monkeypatch):
    """Guards criterion 4: success returns normalized text, request id, and an ok audit row."""
    user = await _seed(async_session_maker, history=True)
    _configured(monkeypatch)
    calls = []
    _client(
        monkeypatch,
        ai_api_client.AiApiResult(
            "ok", {"summary": " Summary ", "suggested_next_step": " Next "}, "request-ok"
        ),
        calls,
    )
    async with async_session_maker() as session:
        result = await generate_contact_summary(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert (result.category, result.request_id, result.summary, result.suggested_next_step) == (
        "ok",
        "request-ok",
        "Summary",
        "Next",
    )
    assert (audit.feature, audit.outcome, audit.request_id) == (SUMMARY_FEATURE, "ok", "request-ok")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "error", "unavailable"])
@pytest.mark.parametrize("truncated", [False, True])
async def test_all_called_outcomes_preserve_snapshot_truncated(monkeypatch, outcome, truncated):
    """Guards criterion 5: success, error, and unavailable return snapshot truncation unchanged."""
    snapshot = {
        "deals": [],
        "open_tasks": [],
        "documents": [],
        "activities": [{"type": "phone"}],
        "messages": [],
        "truncated": truncated,
    }
    user = _user()
    monkeypatch.setattr(
        contact_summary_service,
        "build_contact_snapshot",
        lambda *args, **kwargs: _snapshot(snapshot),
    )
    _configured(monkeypatch)
    _client(
        monkeypatch,
        ai_api_client.AiApiResult(
            outcome,
            {"summary": "s", "suggested_next_step": ""} if outcome == "ok" else None,
        ),
        [],
    )

    class Database:
        async def flush(self):
            pass

        def add(self, value):
            pass

    result = await generate_contact_summary(
        Database(), user=user, contact_id="contact", output_language="en"
    )
    assert result.category == outcome and result.truncated is truncated


async def _snapshot(value):
    return value
