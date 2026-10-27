"""CRM-TASK-075 tests for drafting follow-up records from chat messages."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.activity import Activity
from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.user import User
from app.services import ai_api_client, ai_assist_service, followup_draft_service
from app.services.ai_features import (
    FOLLOWUP_CHAT_FEATURE,
    FOLLOWUP_CHAT_SCHEMA,
    FOLLOWUP_CHAT_SYSTEM,
    validate_followup_chat,
)
from app.services.followup_draft_service import generate_followup_from_chat

BASE = datetime(2026, 10, 20, 6, 12, 0)
TODAY = "2026-01-05"


def _output(**changes):
    output = {
        "content": "Followed up",
        "tasks": [{"title": "Send quote", "due_date": None, "priority": "mid"}],
    }
    output.update(changes)
    return output


def _task(due_date=None):
    return {"title": "x", "due_date": due_date, "priority": "low"}


@pytest.mark.parametrize(
    ("output", "valid"),
    [
        (_output(content="x" * 1000), True),
        (_output(content="x" * 1001), False),
        (_output(tasks=[_task()] * 3), True),
        (_output(tasks=[_task()] * 4), False),
        (_output(tasks=[_task(TODAY)]), True),
        (_output(tasks=[_task("2026-01-04")]), False),
        (_output(activity_type="WhatsApp"), False),
        ({"content": "x"}, False),
        ([], False),
        ("text", False),
    ],
)
def test_validate_followup_chat(output, valid):
    """Guards criteria 1-2: from-chat output shape and the shared content and task bounds."""
    assert (validate_followup_chat(output, TODAY) is not None) is valid


def _message(index, *, contact_id="contact", channel="whatsapp", at=None, body=None):
    return Message(
        id=f"{contact_id}-message-{index:03d}",
        contact_id=contact_id,
        channel=channel,
        direction="inbound",
        sender_id="sender",
        recipient_id="recipient",
        subject="Subject" if channel == "email" else None,
        body=body if body is not None else f"body {index}",
        author_kind="customer",
        created_at=at or BASE + timedelta(minutes=index + 1),
    )


def _activity(activity_id, at, activity_type="phone", user_id="user", deal_id="deal"):
    return Activity(
        id=activity_id,
        contact_id="contact",
        user_id=user_id,
        type=activity_type,
        deal_id=deal_id,
        created_at=at,
    )


async def _seed(maker, *rows):
    user = User(
        id="user", name="User", email="user@example.test", password_hash="hash", role="sales"
    )
    async with maker() as session:
        session.add_all(
            [
                user,
                Contact(id="contact", name="Contact", company="Company"),
                Contact(id="other-contact", name="Other"),
                Deal(
                    id="deal",
                    contact_id="contact",
                    title="Deal",
                    status="following",
                    amount=Decimal("1"),
                    assigned_to="user",
                ),
                Deal(id="other-contact-deal", contact_id="other-contact", assigned_to="user"),
                *rows,
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


def _today():
    return datetime.utcnow().date().isoformat()


async def _generate(maker, user, monkeypatch, *, output=None, **changes):
    calls = []
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult("ok", output or _output(), "request"), calls)
    arguments = {
        "user": user,
        "contact_id": "contact",
        "deal_id": "deal",
        "today": _today(),
        "utc_offset_minutes": 0,
        "output_language": "en",
    }
    arguments.update(changes)
    async with maker() as session:
        result = await generate_followup_from_chat(session, **arguments)
        await session.commit()
    payload = json.loads(calls[0]["messages"][0]["content"]) if calls else None
    return result, payload, calls


@pytest.mark.asyncio
async def test_messages_since_latest_non_status_change_activity(async_session_maker, monkeypatch):
    """Guards criterion 4: start point, strictly-later messages, and only this contact's messages."""
    start = BASE + timedelta(hours=1)
    user = await _seed(
        async_session_maker,
        _activity("older", BASE),
        _activity("latest", start, user_id="someone-else", deal_id="another-deal"),
        _activity("status", start + timedelta(hours=2), activity_type="status change"),
        _message(1, at=start),
        _message(2, at=start + timedelta(minutes=1)),
        _message(3, at=start + timedelta(hours=3)),
        _message(4, contact_id="other-contact", at=start + timedelta(minutes=5)),
    )
    _, payload, _ = await _generate(async_session_maker, user, monkeypatch)
    assert [message["body"] for message in payload["messages"]] == ["body 2", "body 3"]
    assert payload["since"] == "2026-10-20T07:12:00+00:00"


@pytest.mark.asyncio
async def test_messages_without_activity_use_all(async_session_maker, monkeypatch):
    """Guards criterion 4: without a follow-up record every message of the contact is used."""
    user = await _seed(async_session_maker, _message(1), _message(2))
    result, payload, _ = await _generate(async_session_maker, user, monkeypatch)
    assert [message["body"] for message in payload["messages"]] == ["body 1", "body 2"]
    assert payload["since"] is None and result.since is None


@pytest.mark.asyncio
async def test_more_than_forty_messages_keep_latest_forty(async_session_maker, monkeypatch):
    """Guards criterion 4: 41 messages send the latest 40 in ascending order and truncate."""
    user = await _seed(async_session_maker, *[_message(index) for index in range(41)])
    result, payload, _ = await _generate(async_session_maker, user, monkeypatch)
    assert [message["body"] for message in payload["messages"]] == [
        f"body {index}" for index in range(1, 41)
    ]
    assert payload["truncated"] is True and result.truncated is True


@pytest.mark.asyncio
async def test_payload_over_limit_drops_oldest(async_session_maker, monkeypatch):
    """Guards criterion 4: over 16 KiB the oldest messages are dropped and truncated is set."""
    user = await _seed(
        async_session_maker,
        *[_message(index, body=f"{index}:" + "x" * 1900) for index in range(20)],
    )
    result, payload, calls = await _generate(async_session_maker, user, monkeypatch)
    content = calls[0]["messages"][0]["content"]
    assert 1 <= len(payload["messages"]) < 20
    assert payload["messages"][-1]["body"].startswith("19:")
    assert len(content.encode("utf-8")) <= ai_assist_service.SNAPSHOT_LIMIT_BYTES
    assert payload["truncated"] is True and result.truncated is True


@pytest.mark.asyncio
async def test_payload_over_limit_keeps_latest_message(async_session_maker, monkeypatch):
    """Guards criterion 4: the newest message is always kept even when still over the limit."""
    user = await _seed(async_session_maker, _message(1), _message(2), _message(3))
    monkeypatch.setattr(ai_assist_service, "SNAPSHOT_LIMIT_BYTES", 10)
    result, payload, _ = await _generate(async_session_maker, user, monkeypatch)
    assert [message["body"] for message in payload["messages"]] == ["body 3"]
    assert payload["truncated"] is True and result.truncated is True


@pytest.mark.asyncio
async def test_long_message_body_sets_truncated(async_session_maker, monkeypatch):
    """Guards criterion 4: a body over 2000 characters reported as truncated sets truncated."""
    user = await _seed(async_session_maker, _message(1, body="x" * 2001))
    result, payload, _ = await _generate(async_session_maker, user, monkeypatch)
    assert len(payload["messages"][0]["body"]) == 2000
    assert payload["truncated"] is True and result.truncated is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offset", "message_at", "since"),
    [
        (480, "2026-10-21T01:30:00+08:00", "2026-10-20T14:12:00+08:00"),
        (0, "2026-10-20T17:30:00+00:00", "2026-10-20T06:12:00+00:00"),
    ],
)
async def test_local_time_in_payload(async_session_maker, monkeypatch, offset, message_at, since):
    """Guards criterion 4: message at and since are local ISO times; as_of and result stay UTC."""
    user = await _seed(
        async_session_maker,
        _activity("latest", BASE),
        _message(1, at=datetime(2026, 10, 20, 17, 30, 0)),
    )
    result, payload, _ = await _generate(
        async_session_maker, user, monkeypatch, utc_offset_minutes=offset
    )
    assert payload["messages"][0]["at"] == message_at and payload["since"] == since
    assert payload["as_of"].endswith("Z") and result.since == "2026-10-20T06:12:00Z"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channels", "activity_type"),
    [
        (["whatsapp", "whatsapp"], "WhatsApp"),
        (["email", "email"], "email"),
        (["email", "whatsapp"], "WhatsApp"),
        (["whatsapp", "email"], "email"),
    ],
)
async def test_activity_type_from_channels(
    async_session_maker, monkeypatch, channels, activity_type
):
    """Guards criterion 5: the CRM picks the follow-up type from the message channels."""
    user = await _seed(
        async_session_maker,
        *[_message(index, channel=channel) for index, channel in enumerate(channels)],
    )
    result, _, _ = await _generate(async_session_maker, user, monkeypatch)
    assert result.activity_type == activity_type


@pytest.mark.asyncio
async def test_request_contract(async_session_maker, monkeypatch):
    """Guards criterion 5: feature, max_tokens, schema, untrusted prompt, and nine payload keys."""
    user = await _seed(async_session_maker, _message(1))
    _, payload, calls = await _generate(async_session_maker, user, monkeypatch)
    call = calls[0]
    assert call["feature"] == FOLLOWUP_CHAT_FEATURE == "crm.followup_from_chat"
    assert call["max_tokens"] == 4000 and call["schema"] == FOLLOWUP_CHAT_SCHEMA
    assert call["system"] == FOLLOWUP_CHAT_SYSTEM and "untrusted data" in call["system"]
    assert set(payload) == {
        "as_of",
        "today",
        "output_language",
        "contact",
        "deal",
        "open_tasks",
        "since",
        "messages",
        "truncated",
    }
    assert payload["contact"] == {"name": "Contact", "company": "Company"}


@pytest.mark.asyncio
async def test_preflight_order(async_session_maker, monkeypatch):
    """Guards criterion 3: invalid input, configuration, and no messages call nothing and audit nothing."""
    user = await _seed(async_session_maker)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", _output(), "request"), calls)
    common = {"user": user, "contact_id": "contact", "today": _today()}
    async with async_session_maker() as session:
        _configured(monkeypatch, False)
        invalid = [
            await generate_followup_from_chat(
                session, deal_id="deal", utc_offset_minutes=0, output_language="fr", **common
            ),
            await generate_followup_from_chat(
                session,
                deal_id="other-contact-deal",
                utc_offset_minutes=0,
                output_language="en",
                **common,
            ),
            *[
                await generate_followup_from_chat(
                    session,
                    deal_id="deal",
                    utc_offset_minutes=value,
                    output_language="en",
                    **common,
                )
                for value in (841, -721, True, "480")
            ],
        ]
        unconfigured = [
            await generate_followup_from_chat(
                session, deal_id="deal", utc_offset_minutes=value, output_language="en", **common
            )
            for value in (840, -720, 0)
        ]
        _configured(monkeypatch)
        session.add_all([_message(1), _activity("latest", BASE + timedelta(hours=1))])
        await session.flush()
        no_messages = await generate_followup_from_chat(
            session, deal_id="deal", utc_offset_minutes=0, output_language="en", **common
        )
        with pytest.raises(ValueError):
            await generate_followup_from_chat(
                session,
                user=user,
                contact_id="missing",
                deal_id="deal",
                today=_today(),
                utc_offset_minutes=0,
                output_language="en",
            )
        await session.commit()
        audits = (await session.execute(select(AiRequest))).scalars().all()
    assert [result.category for result in invalid] == ["invalid_input"] * 6
    assert [result.category for result in unconfigured] == ["not_configured"] * 3
    assert no_messages.category == "no_messages"
    assert calls == [] and audits == []


@pytest.mark.asyncio
async def test_success_result_and_audit(async_session_maker, monkeypatch):
    """Guards criterion 5: success returns CRM type, normalized text, since, truncated, one audit."""
    user = await _seed(async_session_maker, _activity("latest", BASE), _message(1))
    due = (date.fromisoformat(_today()) + timedelta(days=1)).isoformat()
    output = _output(
        content=" Followed up ",
        tasks=[{"title": " Send quote ", "due_date": due, "priority": "high"}],
    )
    result, _, _ = await _generate(async_session_maker, user, monkeypatch, output=output)
    async with async_session_maker() as session:
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == "ok" and result.request_id == "request"
    assert result.activity_type == "WhatsApp" and result.content == "Followed up"
    assert result.tasks == [{"title": "Send quote", "due_date": due, "priority": "high"}]
    assert result.since == "2026-10-20T06:12:00Z" and result.truncated is False
    assert audit.outcome == "ok" and audit.request_id == "request"
    assert audit.feature == FOLLOWUP_CHAT_FEATURE and audit.contact_id == "contact"


@pytest.mark.asyncio
async def test_invalid_output_is_audited_error(async_session_maker, monkeypatch):
    """Guards criterion 5: a successful call with invalid output becomes an audited error."""
    user = await _seed(async_session_maker, _message(1))
    result, _, _ = await _generate(
        async_session_maker, user, monkeypatch, output=_output(activity_type="WhatsApp")
    )
    async with async_session_maker() as session:
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == "error" and result.content is None
    assert result.request_id == audit.request_id == "request"
    assert audit.outcome == "error" and audit.feature == FOLLOWUP_CHAT_FEATURE


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "unavailable"])
async def test_client_failure_result_and_audit(async_session_maker, monkeypatch, outcome):
    """Guards criterion 5: client error and unavailable keep the category and write one audit."""
    user = await _seed(async_session_maker, _message(1))
    calls = []
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult(outcome, None, "request"), calls)
    async with async_session_maker() as session:
        result = await generate_followup_from_chat(
            session,
            user=user,
            contact_id="contact",
            deal_id="deal",
            today=_today(),
            utc_offset_minutes=0,
            output_language="en",
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == audit.outcome == outcome
    assert result.request_id == audit.request_id == "request"
    assert audit.feature == FOLLOWUP_CHAT_FEATURE and len(calls) == 1
