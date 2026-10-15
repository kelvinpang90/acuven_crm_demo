"""CRM-TASK-032 tests for follow-up AI draft business rules."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.task import Task
from app.models.user import User
from app.services import ai_api_client, followup_draft_service
from app.services.ai_features import (
    FOLLOWUP_FEATURE,
    FOLLOWUP_SCHEMA,
    FOLLOWUP_SYSTEM,
    MAX_TOKENS,
    validate_followup_draft,
)
from app.services.followup_draft_service import generate_followup_draft


def _output(**changes):
    output = {
        "activity_type": "phone",
        "content": "Followed up",
        "tasks": [{"title": "Call back", "due_date": None, "priority": "mid"}],
    }
    output.update(changes)
    return output


@pytest.mark.parametrize(
    ("output", "valid"),
    [
        (_output(content="x"), True),
        (_output(content="x" * 1000), True),
        (_output(content="   "), False),
        (_output(content="x" * 1001), False),
        *[
            (_output(activity_type=value), True)
            for value in ["phone", "email", "meeting", "WhatsApp", "other"]
        ],
        (_output(activity_type="status change"), False),
        (_output(activity_type="invalid"), False),
        (_output(tasks=[]), True),
        (_output(tasks=[_output()["tasks"][0]] * 3), True),
        (_output(tasks=[_output()["tasks"][0]] * 4), False),
        (_output(tasks=[{"title": "x" * 100, "due_date": None, "priority": "high"}]), True),
        (_output(tasks=[{"title": " ", "due_date": None, "priority": "mid"}]), False),
        (_output(tasks=[{"title": "x" * 101, "due_date": None, "priority": "mid"}]), False),
        (_output(tasks=[{"title": "x", "due_date": "2026-01-05", "priority": "low"}]), True),
        (_output(tasks=[{"title": "x", "due_date": "2027-01-05", "priority": "low"}]), True),
        (_output(tasks=[{"title": "x", "due_date": "2026-01-04", "priority": "low"}]), False),
        (_output(tasks=[{"title": "x", "due_date": "2027-01-06", "priority": "low"}]), False),
        (_output(tasks=[{"title": "x", "due_date": "2026-02-30", "priority": "low"}]), False),
        (_output(tasks=[{"title": "x", "due_date": "2026/01/05", "priority": "low"}]), False),
        (_output(tasks=[{"title": "x", "due_date": 1, "priority": "low"}]), False),
        (_output(tasks=[{"title": "x", "due_date": None, "priority": "invalid"}]), False),
        ({"content": "x", "tasks": []}, False),
        (_output(extra="x"), False),
        ([], False),
    ],
)
def test_validate_followup_draft_boundaries_and_shape(output, valid):
    """Guards criterion 1: strict complete output validation and bounds."""
    assert (validate_followup_draft(output, "2026-01-05") is not None) is valid


def test_validate_followup_draft_strips_text():
    """Guards criterion 1: accepted content and task titles are normalized."""
    output = validate_followup_draft(
        _output(
            content=" Content ", tasks=[{"title": " Task ", "due_date": None, "priority": "mid"}]
        ),
        "2026-01-05",
    )
    assert output == _output(
        content="Content", tasks=[{"title": "Task", "due_date": None, "priority": "mid"}]
    )


async def _seed(maker, *, deleted=False, tasks=()):
    user = User(
        id="user", name="User", email="user@example.test", password_hash="hash", role="sales"
    )
    async with maker() as session:
        session.add_all(
            [
                user,
                Contact(
                    id="contact",
                    name="Contact",
                    company="Company",
                    deleted_at=datetime.utcnow() if deleted else None,
                ),
                Deal(
                    id="deal",
                    contact_id="contact",
                    title="Deal",
                    status="following",
                    amount=Decimal("1"),
                    assigned_to="user",
                ),
                *tasks,
            ]
        )
        await session.commit()
    return user


def _configured(monkeypatch, enabled=True):
    monkeypatch.setattr(followup_draft_service.ai_api_client, "is_ai_configured", lambda: enabled)


def _client(monkeypatch, result, calls):
    async def send_message(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(followup_draft_service.ai_api_client, "send_message", send_message)


@pytest.mark.asyncio
async def test_request_contract_and_open_tasks(async_session_maker, monkeypatch):
    """Guards criterion 2: only visible unfinished ordered tasks enter the exact payload."""
    tasks = [
        Task(
            id="late",
            title="late",
            contact_id="contact",
            assigned_to="user",
            due_date=date.today() + timedelta(days=2),
        ),
        Task(id="none", title="none", contact_id="contact", assigned_to="user"),
        Task(
            id="early",
            title="early",
            contact_id="contact",
            assigned_to="user",
            due_date=date.today() + timedelta(days=1),
        ),
        Task(id="done", title="done", contact_id="contact", assigned_to="user", is_done=True),
        Task(id="other", title="other", contact_id="contact", assigned_to="other"),
        *[
            Task(id=f"more-{index}", title=str(index), contact_id="contact", assigned_to="user")
            for index in range(5)
        ],
    ]
    user = await _seed(async_session_maker, tasks=tasks)
    calls = []
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult("ok", _output(), "request"), calls)
    async with async_session_maker() as session:
        await generate_followup_draft(
            session,
            user=user,
            contact_id="contact",
            deal_id="deal",
            note=" note ",
            today=date.today().isoformat(),
            output_language="en",
        )
    call = calls[0]
    payload = json.loads(call["messages"][0]["content"])
    assert call["feature"] == FOLLOWUP_FEATURE and call["max_tokens"] == MAX_TOKENS == 4000
    assert call["schema"] == FOLLOWUP_SCHEMA
    assert "untrusted data" in call["system"] and call["system"] == FOLLOWUP_SYSTEM
    assert set(payload) == {
        "as_of",
        "today",
        "output_language",
        "contact",
        "deal",
        "open_tasks",
        "note",
    }
    assert payload["note"] == "note" and set(payload["contact"]) == {"name", "company"}
    assert [task["title"] for task in payload["open_tasks"]] == ["early", "late", "0", "1", "2"]


@pytest.mark.asyncio
async def test_preflight_order_and_contact_lookup(async_session_maker, monkeypatch):
    """Guards criterion 3: contact-first invalid inputs and configuration cause no side effects."""
    user = await _seed(async_session_maker)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", _output()), calls)
    async with async_session_maker() as session:
        _configured(monkeypatch, False)
        invalid = await generate_followup_draft(
            session,
            user=user,
            contact_id="contact",
            deal_id="deal",
            note="note",
            today=date.today().isoformat(),
            output_language="fr",
        )
        unconfigured = await generate_followup_draft(
            session,
            user=user,
            contact_id="contact",
            deal_id="deal",
            note="note",
            today=date.today().isoformat(),
            output_language="en",
        )
        with pytest.raises(ValueError):
            await generate_followup_draft(
                session,
                user=user,
                contact_id="missing",
                deal_id="deal",
                note="note",
                today=date.today().isoformat(),
                output_language="fr",
            )
        await session.commit()
        audits = (await session.execute(select(AiRequest))).scalars().all()
    assert [invalid.category, unconfigured.category] == ["invalid_input", "not_configured"]
    assert calls == [] and audits == []


@pytest.mark.asyncio
async def test_preflight_parameter_boundaries_and_deal_scope(async_session_maker, monkeypatch):
    """Guards criterion 3: all parameter and selected-deal failures precede configuration."""
    user = await _seed(async_session_maker)
    calls = []
    _configured(monkeypatch, False)
    _client(monkeypatch, ai_api_client.AiApiResult("ok", _output()), calls)
    current = date.today()
    async with async_session_maker() as session:
        session.add_all(
            [
                Contact(id="other-contact", name="Other"),
                Deal(id="other-contact-deal", contact_id="other-contact", assigned_to="user"),
                Deal(
                    id="deleted-deal",
                    contact_id="contact",
                    assigned_to="user",
                    deleted_at=datetime.utcnow(),
                ),
                Deal(id="other-owner-deal", contact_id="contact", assigned_to="other"),
            ]
        )
        await session.commit()
        common = {"user": user, "contact_id": "contact", "note": "note", "output_language": "en"}
        results = [
            await generate_followup_draft(
                session,
                deal_id="deal",
                today=current.isoformat(),
                user=user,
                contact_id="contact",
                note=value,
                output_language="en",
            )
            for value in ("   ", "n" * 2001)
        ]
        results.extend(
            [
                await generate_followup_draft(session, deal_id="deal", today="2026-1-5", **common),
                await generate_followup_draft(
                    session,
                    deal_id="deal",
                    today=(current + timedelta(days=2)).isoformat(),
                    **common,
                ),
                await generate_followup_draft(
                    session, deal_id="other-contact-deal", today=current.isoformat(), **common
                ),
                await generate_followup_draft(
                    session, deal_id="deleted-deal", today=current.isoformat(), **common
                ),
                await generate_followup_draft(
                    session, deal_id="other-owner-deal", today=current.isoformat(), **common
                ),
            ]
        )
        results.extend(
            [
                await generate_followup_draft(
                    session,
                    deal_id="deal",
                    today=(current - timedelta(days=1)).isoformat(),
                    **common,
                ),
                await generate_followup_draft(
                    session,
                    deal_id="deal",
                    today=(current + timedelta(days=1)).isoformat(),
                    **common,
                ),
            ]
        )
        await session.commit()
        audits = (await session.execute(select(AiRequest))).scalars().all()
    assert [result.category for result in results[:-2]] == ["invalid_input"] * 7
    assert [result.category for result in results[-2:]] == ["not_configured", "not_configured"]
    assert calls == [] and audits == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "error", "unavailable"])
async def test_result_and_audit(async_session_maker, monkeypatch, outcome):
    """Guards criterion 4: called outcomes retain request ids and have one audit row."""
    user = await _seed(async_session_maker)
    calls = []
    _configured(monkeypatch)
    _client(
        monkeypatch,
        ai_api_client.AiApiResult(outcome, _output() if outcome == "ok" else None, "request"),
        calls,
    )
    async with async_session_maker() as session:
        result = await generate_followup_draft(
            session,
            user=user,
            contact_id="contact",
            deal_id="deal",
            note="note",
            today=date.today().isoformat(),
            output_language="en",
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert (
        result.category == audit.outcome == outcome
        and result.request_id == audit.request_id == "request"
    )
    assert audit.feature == FOLLOWUP_FEATURE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        _output(content=""),
        _output(tasks=[_output()["tasks"][0]] * 4),
        _output(
            tasks=[
                {
                    "title": "task",
                    "due_date": (date.today() - timedelta(days=1)).isoformat(),
                    "priority": "mid",
                }
            ]
        ),
        _output(extra="extra"),
    ],
)
async def test_invalid_model_output_is_audited_error(async_session_maker, monkeypatch, output):
    """Guards criterion 4: listed invalid successful outputs become audited errors."""
    user = await _seed(async_session_maker)
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult("ok", output, "request-invalid"), [])
    async with async_session_maker() as session:
        result = await generate_followup_draft(
            session,
            user=user,
            contact_id="contact",
            deal_id="deal",
            note="note",
            today=date.today().isoformat(),
            output_language="en",
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == audit.outcome == "error"
    assert result.content is None and result.request_id == audit.request_id == "request-invalid"
