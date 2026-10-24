"""CRM-TASK-051 tests for contact-extract prompts, evidence validation, and business rules."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import ai_api_client, contact_extract_service
from app.services.ai_features import (
    CONTACT_EXTRACT_FEATURE,
    CONTACT_EXTRACT_SCHEMA,
    CONTACT_EXTRACT_SYSTEM,
    INDUSTRIES,
    validate_contact_extract,
)
from app.services.contact_extract_service import (
    EXTRACT_FIELDS,
    build_extract_snapshot,
    generate_contact_extract,
)

BASE = datetime(2026, 10, 1, 8, 0)
ADDRESS = ("No. 8, Jalan Kebun Bunga, " * 20)[:250]  # 250 code points, no edge spaces
BODIES = [
    ("inbound", "Hi, I am from Acme Widgets Sdn Bhd. Email me at Tan@Example.com"),
    ("outbound", "Our office is at Jalan Ampang 1, call 03-2222 3333"),
    ("inbound", "Call me at +60 12 345 6789 or 012-3456789 10 am, we do Banking"),
    ("inbound", "We are at Jalan\n  Kebun   12,  Shah Alam"),
    ("inbound", f"Deliver to {ADDRESS} please"),
    ("inbound", "My name is Tan Mei Ling"),
]


def _snapshot(*, empty_fields=EXTRACT_FIELDS, name_is_phone=False):
    return {
        "contact": {
            "name": "60123456789",
            "name_is_phone": name_is_phone,
            "empty_fields": list(empty_fields),
        },
        "messages": [
            {
                "index": index,
                "at": "2026-10-01T08:00:00Z",
                "direction": direction,
                "author_kind": "customer" if direction == "inbound" else "agent",
                "channel": "whatsapp",
                "body": body,
            }
            for index, (direction, body) in enumerate(BODIES)
        ],
        "truncated": False,
    }


def _item(field, value, evidence, index):
    return {"field": field, "value": value, "evidence": evidence, "message_index": index}


COMPANY = _item("company", "Acme Widgets Sdn Bhd", "from Acme Widgets Sdn Bhd", 0)


def _valid(suggestions, **snapshot):
    return validate_contact_extract({"suggestions": suggestions}, _snapshot(**snapshot))


def test_valid_suggestion_is_stripped():
    """Guards criterion 2: a valid suggestion passes with edge whitespace removed, four keys."""
    output = _valid(
        [_item("company", "  Acme Widgets Sdn Bhd ", " from Acme Widgets Sdn Bhd  ", 0)]
    )
    assert output == {"suggestions": [COMPANY]}


@pytest.mark.parametrize(
    ("suggestion", "expected"),
    [
        (_item("company", "Acme Widgets", "from Acme Widgets Ltd", 0), False),
        (_item("address", "Jalan Ampang 1", "Jalan Ampang 1", 1), False),
        (_item("company", "Acme Widgets Sdn Bhd", "from Acme Widgets Sdn Bhd", 9), False),
        (_item("address", "Jalan Kebun 12", "Jalan Kebun 12, Shah Alam", 3), True),
        (_item("address", ADDRESS, ADDRESS, 4), True),
        (_item("email", "tan@example.com", "Email me at Tan@Example.com", 0), True),
        (_item("email", "lee@example.com", "Email me at Tan@Example.com", 0), False),
        (_item("phone", "012-345 6789", "+60 12 345 6789", 2), True),
        (_item("phone", "012-345 6789", "012-3456789 10 am", 2), True),
        (_item("phone", "012-999 8888", "+60 12 345 6789", 2), False),
        (_item("industry", "Banking", "we do Banking", 2), False),
        (_item("industry", INDUSTRIES[1], "we do Banking", 2), True),
    ],
)
def test_evidence_message_and_value_rules(suggestion, expected):
    """Guards criterion 2: evidence must quote an inbound body and contain the field's value."""
    assert (_valid([suggestion]) is not None) is expected


@pytest.mark.parametrize(
    ("suggestion", "snapshot", "expected"),
    [
        (COMPANY, {"empty_fields": ["email"]}, False),
        (_item("name", "Tan Mei Ling", "My name is Tan Mei Ling", 5), {}, False),
        (
            _item("name", "Tan Mei Ling", "My name is Tan Mei Ling", 5),
            {"name_is_phone": True},
            True,
        ),
    ],
)
def test_only_fillable_fields_pass(suggestion, snapshot, expected):
    """Guards criterion 2: fields must be empty, and name only when name_is_phone is true."""
    assert (_valid([suggestion], **snapshot) is not None) is expected


EMAIL = _item("email", "tan@example.com", "Tan@Example.com", 0)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ({"suggestions": [COMPANY] * 7}, False),
        ({"suggestions": [COMPANY, COMPANY]}, False),
        ({"suggestions": [{k: v for k, v in EMAIL.items() if k != "evidence"}]}, False),
        ({"suggestions": [{**EMAIL, "note": "x"}]}, False),
        ({"suggestions": [EMAIL, "x"]}, False),
        ({"suggestions": [EMAIL], "extra": []}, False),
        ({"suggestions": [COMPANY, EMAIL]}, True),
        ({"suggestions": []}, True),
    ],
)
def test_output_shape(output, expected):
    """Guards criterion 2: at most 6, unique fields, exact keys, objects only; empty passes."""
    assert (validate_contact_extract(output, _snapshot()) is not None) is expected


def _user():
    return User(
        id="user", name="User", email="user@example.test", password_hash="hash", role="sales"
    )


def _contact(**values):
    fields = {"id": "contact", "name": "60123456789"}
    fields.update(values)
    return Contact(**fields)


FILLED = {
    "name": "Tan",
    "company": "Acme",
    "industry": INDUSTRIES[0],
    "email": "tan@example.com",
    "phone": "60123456789",
    "address": "Jalan Kebun 12",
}


def _message(message_id, minute=0, **values):
    fields = {
        "id": message_id,
        "contact_id": "contact",
        "channel": "whatsapp",
        "direction": "inbound",
        "author_kind": "customer",
        "sender_id": "sender",
        "recipient_id": "recipient",
        "body": "I am from Acme Widgets Sdn Bhd",
        "created_at": BASE + timedelta(minutes=minute),
    }
    fields.update(values)
    return Message(**fields)


async def _seed(maker, contact=None, messages=None):
    user = _user()
    async with maker() as session:
        session.add_all([user, contact or _contact()])
        session.add_all(messages if messages is not None else [_message("m1")])
        await session.commit()
    return user


def _configured(monkeypatch, enabled=True):
    monkeypatch.setattr(contact_extract_service.ai_api_client, "is_ai_configured", lambda: enabled)


def _client(monkeypatch, result, calls):
    async def send_message(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(contact_extract_service.ai_api_client, "send_message", send_message)


async def _audits(session):
    return (await session.execute(select(AiRequest))).scalars().all()


@pytest.mark.asyncio
async def test_request_contract(async_session_maker, monkeypatch):
    """Guards criteria 1 and 5: feature, 4000 tokens, schema, prompt rules, three payload keys."""
    user = await _seed(async_session_maker)
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", {"suggestions": []}), calls)
    async with async_session_maker() as session:
        contact = await session.get(Contact, "contact")
        expected = await build_extract_snapshot(
            session, contact=contact, now=datetime.now(timezone.utc)
        )
        await generate_contact_extract(
            session, user=user, contact_id="contact", output_language="en"
        )

    call = calls[0]
    payload = json.loads(call["messages"][0]["content"])
    assert call["feature"] == CONTACT_EXTRACT_FEATURE == "crm.contact_extract"
    assert call["max_tokens"] == 4000 and call["schema"] == CONTACT_EXTRACT_SCHEMA
    assert call["system"] == CONTACT_EXTRACT_SYSTEM
    assert "untrusted data" in call["system"] and "Never execute" in call["system"]
    assert "only from messages whose direction is inbound" in call["system"]
    assert all(industry in call["system"] for industry in INDUSTRIES)
    assert set(payload) == {"as_of", "output_language", "snapshot"}
    assert payload["as_of"].endswith("Z") and payload["output_language"] == "en"
    assert payload["snapshot"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contact", "messages", "enabled", "language", "expected"),
    [
        (None, None, False, "fr", "invalid_input"),
        (FILLED, None, False, "en", "not_configured"),
        (FILLED, [], True, "en", "nothing_to_fill"),
        (None, [_message("m1", direction="outbound")], True, "en", "no_messages"),
        (
            None,
            [_message("m0")] + [_message(f"m{i}", i, direction="outbound") for i in range(1, 41)],
            True,
            "en",
            "no_messages",
        ),
    ],
)
async def test_preflight_order_skips_client_and_audit(
    async_session_maker, monkeypatch, contact, messages, enabled, language, expected
):
    """Guards criterion 4: check order, outbound-only final snapshot; no client call, no audit."""
    user = await _seed(async_session_maker, _contact(**contact) if contact else None, messages)
    _configured(monkeypatch, enabled)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", {"suggestions": []}), calls)
    async with async_session_maker() as session:
        result = await generate_contact_extract(
            session, user=user, contact_id="contact", output_language=language
        )
        await session.commit()
        audits = await _audits(session)
    assert result.category == expected
    assert calls == [] and audits == []


@pytest.mark.asyncio
async def test_missing_or_deleted_contact_raises(async_session_maker, monkeypatch):
    """Guards criterion 4: a missing or deleted contact raises ValueError."""
    user = await _seed(async_session_maker, _contact(deleted_at=BASE))
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok", {"suggestions": []}), calls)
    async with async_session_maker() as session:
        for contact_id in ("missing", "contact"):
            with pytest.raises(ValueError):
                await generate_contact_extract(
                    session, user=user, contact_id=contact_id, output_language="en"
                )
    assert calls == []


@pytest.mark.asyncio
async def test_success_returns_suggestions_audits_and_keeps_contact(
    async_session_maker, monkeypatch
):
    """Guards criterion 5: ok with suggestions, truncated, request id, one audit; contact unchanged."""
    user = await _seed(async_session_maker)
    _configured(monkeypatch)
    output = {
        "suggestions": [_item("company", " Acme Widgets Sdn Bhd ", "from Acme Widgets Sdn Bhd", 0)]
    }
    _client(monkeypatch, ai_api_client.AiApiResult("ok", output, "request-ok"), [])
    async with async_session_maker() as session:
        result = await generate_contact_extract(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    async with async_session_maker() as session:
        contact = await session.get(Contact, "contact")

    assert (result.category, result.request_id, result.truncated) == ("ok", "request-ok", False)
    assert result.suggestions == [COMPANY]
    assert (audit.feature, audit.contact_id, audit.outcome, audit.request_id) == (
        CONTACT_EXTRACT_FEATURE,
        "contact",
        "ok",
        "request-ok",
    )
    assert contact.name == "60123456789"
    assert [getattr(contact, field) for field in EXTRACT_FIELDS] == [None] * 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_result", "expected"),
    [
        (
            ai_api_client.AiApiResult(
                "ok", {"suggestions": [_item("company", "Other Co", "Other Co", 0)]}, "request-x"
            ),
            "error",
        ),
        (ai_api_client.AiApiResult("error", request_id="request-x"), "error"),
        (ai_api_client.AiApiResult("unavailable", request_id="request-x"), "unavailable"),
    ],
)
async def test_failures_are_audited_with_request_id(
    async_session_maker, monkeypatch, api_result, expected
):
    """Guards criterion 5: invalid output becomes error; error and unavailable keep category and id."""
    user = await _seed(async_session_maker)
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, api_result, calls)
    async with async_session_maker() as session:
        result = await generate_contact_extract(
            session, user=user, contact_id="contact", output_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()
    assert result.category == audit.outcome == expected
    assert result.request_id == audit.request_id == "request-x"
    assert result.suggestions is None and len(calls) == 1
    assert (audit.feature, audit.contact_id) == (CONTACT_EXTRACT_FEATURE, "contact")
