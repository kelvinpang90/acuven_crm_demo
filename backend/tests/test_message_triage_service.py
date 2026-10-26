"""CRM-TASK-066 tests for conversation intent and urgency labels."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.conversation_triage import ConversationTriage
from app.models.message import Message
from app.services import ai_api_client, message_triage_service
from app.services.ai_features import (
    MESSAGE_TRIAGE_FEATURE,
    MESSAGE_TRIAGE_SCHEMA,
    MESSAGE_TRIAGE_SYSTEM,
    validate_message_triage,
)
from app.services.message_triage_service import (
    build_triage_snapshot,
    classify_conversation,
    run_triage,
)

BASE = datetime.utcnow().replace(microsecond=0) - timedelta(hours=1)
LABELS = {"intent": "complaint", "urgency": "urgent"}


def _message(
    id, *, contact_id="contact", direction="inbound", minutes=0, body="hi", author_kind=None
):
    return Message(
        id=id,
        contact_id=contact_id,
        channel="whatsapp",
        direction=direction,
        sender_id="sender",
        recipient_id="recipient",
        body=body,
        author_kind=author_kind,
        created_at=BASE + timedelta(minutes=minutes),
    )


async def _seed(maker, *items, deleted=False):
    async with maker() as session:
        session.add_all(
            [
                Contact(
                    id="contact",
                    name="Contact",
                    company="Company",
                    deleted_at=BASE if deleted else None,
                ),
                Contact(id="other", name="Other"),
                *items,
            ]
        )
        await session.commit()


def _configured(monkeypatch, enabled=True):
    monkeypatch.setattr(message_triage_service.ai_api_client, "is_ai_configured", lambda: enabled)


def _client(monkeypatch, result, calls, during=None):
    async def send_message(**kwargs):
        calls.append(kwargs)
        if during is not None:
            await during()
        return result

    monkeypatch.setattr(message_triage_service.ai_api_client, "send_message", send_message)


async def _rows(session):
    triage = (await session.execute(select(ConversationTriage))).scalars().all()
    audits = (await session.execute(select(AiRequest))).scalars().all()
    return triage, audits


@pytest.mark.asyncio
async def test_snapshot_keeps_latest_ten_oldest_first(async_session_maker):
    """Guards criterion 1: latest 10 of 11 old-to-new, other customers excluded, 500-char bodies,
    author_kind inferred from direction."""
    messages = [_message(f"m{index:02d}", minutes=index) for index in range(11)]
    messages[5].body = "x" * 501
    messages[6].direction = "outbound"
    messages[7].author_kind = "ai"
    messages[7].direction = "outbound"
    await _seed(async_session_maker, *messages, _message("foreign", contact_id="other", minutes=20))
    async with async_session_maker() as session:
        contact = await session.get(Contact, "contact")
        snapshot = await build_triage_snapshot(session, contact=contact, now=datetime.utcnow())

    assert set(snapshot) == {"contact", "messages"}
    assert snapshot["contact"] == {"name": "Contact", "company": "Company"}
    items = snapshot["messages"]
    assert len(items) == 10
    assert [item["at"] for item in items] == [
        (BASE + timedelta(minutes=index)).isoformat() + "Z" for index in range(1, 11)
    ]
    assert all(set(item) == {"at", "direction", "author_kind", "channel", "body"} for item in items)
    assert items[4]["body"] == "x" * 500
    assert (items[0]["direction"], items[0]["author_kind"]) == ("inbound", "customer")
    assert (items[5]["direction"], items[5]["author_kind"]) == ("outbound", "agent")
    assert items[6]["author_kind"] == "ai"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ({"intent": "quotation", "urgency": "high"}, True),
        ({"intent": "other", "urgency": "normal"}, True),
        ({"intent": "refund", "urgency": "high"}, False),
        ({"intent": "order", "urgency": "critical"}, False),
        ({"intent": "order"}, False),
        ({"urgency": "low"}, False),
        ({"intent": "order", "urgency": "low", "reason": "x"}, False),
        ([], False),
    ],
)
def test_validate_message_triage(output, expected):
    """Guards criterion 2: both values in their enums and exactly the two keys."""
    assert (validate_message_triage(output) is not None) is expected


@pytest.mark.asyncio
async def test_request_contract(async_session_maker, monkeypatch):
    """Guards criteria 2 and 4: feature, max_tokens, schema, untrusted-data prompt, two-key
    user message carrying the server-built snapshot."""
    await _seed(async_session_maker, _message("m1"))
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", dict(LABELS), "req"), calls)
    async with async_session_maker() as session:
        contact = await session.get(Contact, "contact")
        expected = await build_triage_snapshot(session, contact=contact, now=datetime.utcnow())
        await classify_conversation(session, contact_id="contact", message_id="m1")

    call = calls[0]
    payload = json.loads(call["messages"][0]["content"])
    assert call["feature"] == MESSAGE_TRIAGE_FEATURE == "crm.message_triage"
    assert call["max_tokens"] == 4000
    assert call["schema"] == MESSAGE_TRIAGE_SCHEMA
    assert MESSAGE_TRIAGE_SCHEMA["required"] == ["intent", "urgency"]
    assert MESSAGE_TRIAGE_SCHEMA["additionalProperties"] is False
    assert call["system"] == MESSAGE_TRIAGE_SYSTEM and "untrusted data" in call["system"]
    assert set(payload) == {"as_of", "snapshot"} and payload["as_of"].endswith("Z")
    assert payload["snapshot"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deleted", "message_id", "expected"),
    [
        (True, "m1", "skipped"),
        (False, "missing", "skipped"),
        (False, "foreign", "skipped"),
        (False, "out", "skipped"),
        (False, "m1", "superseded"),
    ],
)
async def test_preflight_skips_client_and_audit(
    async_session_maker, monkeypatch, deleted, message_id, expected
):
    """Guards criterion 4: deleted customer, missing or foreign message, outbound message are
    skipped; a newer inbound message supersedes; none calls the client or writes an audit."""
    await _seed(
        async_session_maker,
        _message("m1"),
        _message("m2", minutes=1),
        _message("out", direction="outbound", minutes=2),
        _message("foreign", contact_id="other"),
        deleted=deleted,
    )
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", dict(LABELS)), calls)
    async with async_session_maker() as session:
        result = await classify_conversation(session, contact_id="contact", message_id=message_id)
        await session.commit()
        triage, audits = await _rows(session)
    assert result.category == expected
    assert calls == [] and audits == [] and triage == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "newer",
    [
        _message("later", direction="outbound", minutes=5),
        _message("later", minutes=60 + 10),
    ],
    ids=["outbound", "future"],
)
async def test_outbound_or_future_message_is_not_newer(async_session_maker, monkeypatch, newer):
    """Guards criterion 4: a newer outbound message or one 10 minutes in the future does not
    supersede; with AI unconfigured the result is not_configured without client or audit."""
    await _seed(async_session_maker, _message("m1"), newer)
    _configured(monkeypatch, False)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", dict(LABELS)), calls)
    async with async_session_maker() as session:
        result = await classify_conversation(session, contact_id="contact", message_id="m1")
        await session.commit()
        triage, audits = await _rows(session)
    assert result.category == "not_configured"
    assert calls == [] and audits == [] and triage == []


@pytest.mark.asyncio
async def test_success_inserts_then_overwrites_one_row(async_session_maker, monkeypatch):
    """Guards criterion 4: success inserts the row, a later success overwrites it, audits have
    no user."""
    await _seed(async_session_maker, _message("m1"))
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult("ok", dict(LABELS), "req-1"), [])
    async with async_session_maker() as session:
        first = await classify_conversation(session, contact_id="contact", message_id="m1")
        await session.commit()
        row = (await session.execute(select(ConversationTriage))).scalar_one()
        assert (row.intent, row.urgency, row.message_id, row.request_id) == (
            "complaint",
            "urgent",
            "m1",
            "req-1",
        )
        assert abs(row.triaged_at - datetime.utcnow()) < timedelta(minutes=1)

        session.add(_message("m2", minutes=1))
        await session.commit()
        _client(
            monkeypatch,
            ai_api_client.AiApiResult("ok", {"intent": "other", "urgency": "low"}, "req-2"),
            [],
        )
        second = await classify_conversation(session, contact_id="contact", message_id="m2")
        await session.commit()
        triage, audits = await _rows(session)

    assert (first.category, first.request_id, first.intent, first.urgency) == (
        "ok",
        "req-1",
        "complaint",
        "urgent",
    )
    assert (second.category, second.intent, second.urgency) == ("ok", "other", "low")
    assert len(triage) == 1
    assert (triage[0].contact_id, triage[0].intent, triage[0].message_id) == (
        "contact",
        "other",
        "m2",
    )
    assert [
        (audit.user_id, audit.contact_id, audit.feature, audit.outcome) for audit in audits
    ] == [
        (None, "contact", MESSAGE_TRIAGE_FEATURE, "ok"),
        (None, "contact", MESSAGE_TRIAGE_FEATURE, "ok"),
    ]


@pytest.mark.asyncio
async def test_newer_inbound_during_call_is_superseded(async_session_maker, monkeypatch):
    """Guards criterion 4: a newer inbound message arriving during the AI call stops the write;
    the audit still records ok."""
    await _seed(async_session_maker, _message("m1"))
    _configured(monkeypatch)
    async with async_session_maker() as session:

        async def arrive():
            session.add(_message("m2", minutes=1))

        _client(monkeypatch, ai_api_client.AiApiResult("ok", dict(LABELS), "req"), [], arrive)
        result = await classify_conversation(session, contact_id="contact", message_id="m1")
        await session.commit()
        triage, audits = await _rows(session)
    assert result.category == "superseded" and result.request_id == "req"
    assert triage == []
    assert [(audit.outcome, audit.request_id) for audit in audits] == [("ok", "req")]


@pytest.mark.asyncio
async def test_row_covering_newer_message_is_not_overwritten(async_session_maker, monkeypatch):
    """Guards criterion 4: a row another task wrote for a newer message during the call is kept."""
    await _seed(async_session_maker, _message("m1"))
    _configured(monkeypatch)
    async with async_session_maker() as session:

        async def other_task():
            session.add(_message("m2", minutes=1))
            session.add(
                ConversationTriage(
                    contact_id="contact",
                    intent="order",
                    urgency="high",
                    message_id="m2",
                    request_id="newer",
                    triaged_at=datetime.utcnow(),
                )
            )

        _client(monkeypatch, ai_api_client.AiApiResult("ok", dict(LABELS), "req"), [], other_task)
        result = await classify_conversation(session, contact_id="contact", message_id="m1")
        await session.commit()
        triage, audits = await _rows(session)
    assert result.category == "superseded"
    assert [(row.message_id, row.intent, row.request_id) for row in triage] == [
        ("m2", "order", "newer")
    ]
    assert [audit.outcome for audit in audits] == ["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_result", "expected"),
    [
        (ai_api_client.AiApiResult("ok", {"intent": "refund", "urgency": "low"}, "req"), "error"),
        (ai_api_client.AiApiResult("error", request_id="req"), "error"),
        (ai_api_client.AiApiResult("unavailable", request_id="req"), "unavailable"),
    ],
    ids=["invalid", "error", "unavailable"],
)
async def test_failure_keeps_existing_row(async_session_maker, monkeypatch, api_result, expected):
    """Guards criterion 4: invalid output, error, and unavailable keep the row and audit once."""
    await _seed(
        async_session_maker,
        _message("m0"),
        _message("m1", minutes=1),
        ConversationTriage(
            contact_id="contact",
            intent="order",
            urgency="low",
            message_id="m0",
            request_id="old",
            triaged_at=BASE,
        ),
    )
    _configured(monkeypatch)
    _client(monkeypatch, api_result, [])
    async with async_session_maker() as session:
        result = await classify_conversation(session, contact_id="contact", message_id="m1")
        await session.commit()
        triage, audits = await _rows(session)
    assert (result.category, result.request_id, result.intent) == (expected, "req", None)
    assert [(row.message_id, row.intent, row.urgency, row.request_id) for row in triage] == [
        ("m0", "order", "low", "old")
    ]
    assert [(audit.user_id, audit.outcome, audit.request_id) for audit in audits] == [
        (None, expected, "req")
    ]


def _sleep_recorder(monkeypatch):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(message_triage_service, "asyncio", SimpleNamespace(sleep=sleep))
    return sleeps


@pytest.mark.asyncio
async def test_run_triage_disabled_does_not_classify(monkeypatch):
    """Guards criterion 5: with the switch off classify_conversation is not called."""
    calls = []

    async def classify(db, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(message_triage_service, "classify_conversation", classify)
    monkeypatch.setattr(message_triage_service.settings, "ai_triage_enabled", False)
    _configured(monkeypatch)
    await run_triage("contact", "m1", delay=0)
    assert calls == []


@pytest.mark.asyncio
async def test_run_triage_swallows_errors(async_session_maker, monkeypatch):
    """Guards criterion 5: an exception from classify_conversation does not escape run_triage."""

    async def classify(db, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(message_triage_service, "classify_conversation", classify)
    monkeypatch.setattr(message_triage_service, "AsyncSessionLocal", async_session_maker)
    monkeypatch.setattr(message_triage_service.settings, "ai_triage_enabled", True)
    _configured(monkeypatch)
    await run_triage("contact", "m1", delay=0)


@pytest.mark.asyncio
async def test_run_triage_zero_delay_does_not_wait(async_session_maker, monkeypatch):
    """Guards criterion 5: delay 0 does not sleep; classification runs in its own session and is
    committed."""
    await _seed(async_session_maker, _message("m1"))
    sleeps = _sleep_recorder(monkeypatch)
    monkeypatch.setattr(message_triage_service, "AsyncSessionLocal", async_session_maker)
    monkeypatch.setattr(message_triage_service.settings, "ai_triage_enabled", True)
    _configured(monkeypatch)
    _client(monkeypatch, ai_api_client.AiApiResult("ok", dict(LABELS), "req"), [])
    await run_triage("contact", "m1", delay=0)
    async with async_session_maker() as session:
        triage, audits = await _rows(session)
    assert sleeps == []
    assert [row.message_id for row in triage] == ["m1"] and len(audits) == 1
