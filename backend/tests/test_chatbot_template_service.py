"""CRM-TASK-097: reading chatbot's template whitelist, sending and retrying templates.

Names follow docs/chatbot-phase3.md section 12: contact C1 (owner sales S1 "Mei",
manager M1), now 10:00, last WhatsApp inbound two days ago (window closed).
chatbot is stubbed at the HTTP client; every request's signature is recomputed here.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_send_service
from app.services.chatbot_send_service import (
    CHATBOT_FAILED,
    CLAIMED,
    IN_PROGRESS,
    NO_PHONE,
    NOT_CONFIGURED,
    NOT_FOUND,
    RETRY_EXPIRED,
    SEND_FAILED,
    SENT,
    UNCONFIRMED,
    retry_agent_reply,
)
from app.services.chatbot_template_service import (
    NO_OPT_IN,
    OK,
    TEMPLATE_PARAMS,
    TEMPLATE_PENDING,
    TEMPLATE_UNKNOWN,
    list_templates,
    send_template,
)

NOW = datetime(2026, 11, 1, 10, 0)
PHONE = "60123456789"
BSUID = "MY.visitor"
BASE_URL = "http://chatbot.internal:8000"
SECRET = "shared-send-secret"
S1 = "s1"
M1 = "m1"
TEMPLATES_PATH = "/internal/crm/templates"
SEND_TEMPLATE_PATH = "/internal/crm/send-template"

FOLLOWUP = {
    "name": "crm_followup",
    "language": "en",
    "category": "MARKETING",
    "params": [{"label": "Name", "max_len": 10}, {"label": "Company", "max_len": 20}],
    "body": "Hi {{1}}, {{2}} would like to follow up. {{1}}, reply any time.",
}
FOLLOWUP_ZH = {
    "name": "crm_followup",
    "language": "zh_CN",
    "params": [{"label": "称呼", "max_len": 10}],
    "body": "您好 {{1}}",
}
NO_PARAMS = {"name": "crm_hello", "language": "en", "params": [], "body": "Hello"}
WHITELIST = [FOLLOWUP, FOLLOWUP_ZH, NO_PARAMS]


def _contact(**kwargs):
    values = {
        "id": "c1",
        "name": "Mei",
        "phone": PHONE,
        "whatsapp_bsuid": BSUID,
        "assigned_to": S1,
    }
    values.update(kwargs)
    return Contact(**values)


def _inbound(created_at=NOW - timedelta(days=2), *, message_id="inbound-1", channel="whatsapp"):
    return Message(
        id=message_id,
        contact_id="c1",
        channel=channel,
        direction="inbound",
        sender_id=PHONE,
        recipient_id="ai_chatbot_demo",
        body="hello",
        is_read=True,
        created_at=created_at,
    )


def _template_message(**kwargs):
    values = {
        "id": "msg1",
        "contact_id": "c1",
        "channel": "whatsapp",
        "direction": "outbound",
        "sender_id": "ai_chatbot_demo",
        "recipient_id": PHONE,
        "body": "Hi Mei, Acuven would like to follow up. Mei, reply any time.",
        "is_read": True,
        "author_kind": "agent",
        "sent_by": S1,
        "template_name": "crm_followup",
        "template_language": "en",
        "template_params": ["Mei", "Acuven"],
        "created_at": NOW - timedelta(minutes=30),
    }
    values.update(kwargs)
    return Message(**values)


def _user(user_id, name):
    return User(
        id=user_id, name=name, email=f"{user_id}@example.com", password_hash="x", role="sales"
    )


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _outbound(maker):
    async with maker() as session:
        result = await session.execute(
            select(Message).where(Message.direction == "outbound").order_by(Message.created_at)
        )
        return result.scalars().all()


async def _stored_claim(maker):
    async with maker() as session:
        contact = await session.get(Contact, "c1")
        return contact.reply_claimed_by, contact.reply_claimed_until


def _configured(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_send_url", BASE_URL + "/")
    monkeypatch.setattr(settings, "chatbot_send_secret", SECRET)


def _response(status, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload if payload is not None else {}
    return response


def _sent(wa_message_id="wamid.T1", **extra):
    return _response(200, {"result": "sent", "wa_message_id": wa_message_id, **extra})


def _templates_ok(templates=None):
    return _response(200, {"templates": WHITELIST if templates is None else templates})


def _chatbot(monkeypatch, *, send=None, templates=None):
    """Stub chatbot by path. ``send`` / ``templates`` is a response, an exception to
    raise, or an async callable returning a response. Returns the recorded calls
    as (path, parsed body, raw body).

    The service swallows any Exception from the client, so problems found here
    use pytest.fail, whose exception is not an Exception subclass."""
    calls = []

    async def post(url, *, content, headers):
        expected = hmac.new(
            SECRET.encode(),
            headers["X-Acuven-Timestamp"].encode() + b"." + content,
            hashlib.sha256,
        ).hexdigest()
        if headers["X-Acuven-Signature"] != "sha256=" + expected:
            pytest.fail("chatbot request signature does not match")
        if not url.startswith(BASE_URL + "/internal/"):
            pytest.fail(f"unexpected chatbot url {url}")
        path = url[len(BASE_URL) :]
        calls.append((path, json.loads(content), content))
        outcome = templates if path == TEMPLATES_PATH else send
        if outcome is None and path == TEMPLATES_PATH:
            outcome = _templates_ok()
        if outcome is None:
            pytest.fail(f"unexpected chatbot call to {path}")
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome) and not isinstance(outcome, MagicMock):
            return await outcome()
        return outcome

    client = MagicMock()
    client.return_value.__aenter__.return_value.post = post
    monkeypatch.setattr(
        chatbot_send_service,
        "httpx",
        SimpleNamespace(
            AsyncClient=client,
            Timeout=httpx.Timeout,
            HTTPError=httpx.HTTPError,
            ConnectError=httpx.ConnectError,
            ConnectTimeout=httpx.ConnectTimeout,
            PoolTimeout=httpx.PoolTimeout,
        ),
    )
    return calls


def _paths(calls):
    return [path for path, _, _ in calls]


async def _send(maker, *, agent_id=S1, name="crm_followup", language="en", params=None, now=NOW):
    async with maker() as session:
        return await send_template(
            session,
            contact_id="c1",
            name=name,
            language=language,
            params=["Mei", "Acuven"] if params is None else params,
            agent_id=agent_id,
            agent_name="Mei" if agent_id == S1 else "Lim",
            now=now,
        )


async def _retry(maker, *, requester_id=S1, now=NOW):
    async with maker() as session:
        return await retry_agent_reply(
            session, message_id="msg1", requester_id=requester_id, now=now
        )


# --- Reading the whitelist (AC1) ---------------------------------------------


async def test_list_templates_returns_valid_items_from_a_signed_empty_request(monkeypatch):
    """Guards AC1 (signed POST of an empty object to /internal/crm/templates; a 200 with
    a templates list returns the valid items as given)."""
    _configured(monkeypatch)
    calls = _chatbot(monkeypatch)

    result = await list_templates()

    assert result.category == OK
    assert result.templates == WHITELIST
    assert [(path, raw) for path, _, raw in calls] == [(TEMPLATES_PATH, b"{}")]


async def test_list_templates_of_an_empty_whitelist_is_an_empty_list(monkeypatch):
    """Guards AC1 (an empty whitelist is a valid, empty result)."""
    _configured(monkeypatch)
    _chatbot(monkeypatch, templates=_templates_ok([]))
    result = await list_templates()
    assert (result.category, result.templates) == (OK, [])


INVALID_TEMPLATES = [
    "not a dict",
    {**NO_PARAMS, "name": ""},
    {key: value for key, value in NO_PARAMS.items() if key != "language"},
    {**NO_PARAMS, "body": 7},
    {**NO_PARAMS, "params": "none"},
    {**FOLLOWUP, "params": [{"label": 1, "max_len": 10}, {"label": "Company", "max_len": 20}]},
    {**FOLLOWUP, "params": [{"label": "Name", "max_len": 0}, {"label": "Company", "max_len": 20}]},
    {**FOLLOWUP, "params": [{"label": "Name", "max_len": True}, {"label": "C", "max_len": 20}]},
    {**FOLLOWUP, "params": [{"label": "Name", "max_len": "10"}, {"label": "C", "max_len": 20}]},
    {**FOLLOWUP, "params": ["Name", {"label": "Company", "max_len": 20}]},
    {**FOLLOWUP, "body": "Hi {{1}}"},
    {**FOLLOWUP, "body": "Hi {{1}} {{3}}"},
    {**FOLLOWUP, "body": "Hi {{1}} {{2}} {{3}}"},
    {**NO_PARAMS, "body": "Hello {{1}}"},
]


@pytest.mark.parametrize("invalid", INVALID_TEMPLATES)
async def test_list_templates_drops_invalid_items_with_a_warning(monkeypatch, caplog, invalid):
    """Guards AC1 (an item with a missing or empty name / language / body, a bad params
    entry, or placeholders other than exactly 1..n is dropped and logged at WARNING;
    the valid items are still returned)."""
    _configured(monkeypatch)
    _chatbot(monkeypatch, templates=_templates_ok([FOLLOWUP, invalid, NO_PARAMS]))

    with caplog.at_level(logging.WARNING, logger="app.services.chatbot_template_service"):
        result = await list_templates()

    assert (result.category, result.templates) == (OK, [FOLLOWUP, NO_PARAMS])
    own = [r for r in caplog.records if r.name == "app.services.chatbot_template_service"]
    assert [record.levelno for record in own] == [logging.WARNING]


async def test_list_templates_not_configured_makes_no_request(monkeypatch):
    """Guards AC1 (unconfigured is NOT_CONFIGURED without calling chatbot)."""
    monkeypatch.setattr(settings, "chatbot_send_url", "")
    monkeypatch.setattr(settings, "chatbot_send_secret", SECRET)
    calls = _chatbot(monkeypatch)
    assert (await list_templates()).category == NOT_CONFIGURED
    assert calls == []


@pytest.mark.parametrize(
    "outcome",
    [
        _response(500, {"templates": WHITELIST}),
        _response(503),
        _response(200, {"templates": {"name": "crm_followup"}}),
        _response(200, {}),
        _response(200, ["not", "an", "object"]),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("refused"),
        TimeoutError(),
    ],
    ids=[
        "500",
        "503",
        "templates-not-list",
        "no-templates",
        "body-not-object",
        "timeout",
        "connect",
        "total-timeout",
    ],
)
async def test_list_templates_failures_are_chatbot_failed(monkeypatch, outcome):
    """Guards AC1 (non-200, timeout, transport failure or a malformed body is
    CHATBOT_FAILED)."""
    _configured(monkeypatch)
    _chatbot(monkeypatch, templates=outcome)
    result = await list_templates()
    assert (result.category, result.templates) == (CHATBOT_FAILED, [])


# --- Send checks (AC2) --------------------------------------------------------


REJECTIONS = [
    # (id, rows, kwargs for _send, configured, templates response, expected, reads whitelist)
    ("missing", [], {}, True, None, NOT_FOUND, False),
    ("deleted", [_contact(deleted_at=NOW), _inbound()], {}, True, None, NOT_FOUND, False),
    (
        "claimed",
        [_contact(reply_claimed_by=M1, reply_claimed_until=NOW + timedelta(minutes=5)), _inbound()],
        {},
        True,
        None,
        CLAIMED,
        False,
    ),
    ("no-phone", [_contact(phone="bad"), _inbound()], {}, True, None, NO_PHONE, False),
    ("no-opt-in", [_contact()], {}, True, None, NO_OPT_IN, False),
    (
        "only-email-inbound",
        [_contact(), _inbound(channel="email")],
        {},
        True,
        None,
        NO_OPT_IN,
        False,
    ),
    (
        "pending-unconfirmed",
        [_contact(), _inbound(), _template_message()],
        {},
        True,
        None,
        TEMPLATE_PENDING,
        False,
    ),
    (
        "pending-confirmed",
        [
            _contact(),
            _inbound(),
            _template_message(external_id="wamid.old", delivery_status="read"),
        ],
        {},
        True,
        None,
        TEMPLATE_PENDING,
        False,
    ),
    ("not-configured", [_contact(), _inbound()], {}, False, None, NOT_CONFIGURED, False),
    (
        "whitelist-failed",
        [_contact(), _inbound()],
        {},
        True,
        _response(500),
        CHATBOT_FAILED,
        True,
    ),
    (
        "unknown-name",
        [_contact(), _inbound()],
        {"name": "crm_other"},
        True,
        None,
        TEMPLATE_UNKNOWN,
        True,
    ),
    (
        "unknown-language",
        [_contact(), _inbound()],
        {"language": "ms"},
        True,
        None,
        TEMPLATE_UNKNOWN,
        True,
    ),
    (
        "dropped-invalid-item",
        [_contact(), _inbound()],
        {},
        True,
        _templates_ok([{**FOLLOWUP, "body": "Hi {{1}}"}]),
        TEMPLATE_UNKNOWN,
        True,
    ),
    (
        "too-few-params",
        [_contact(), _inbound()],
        {"params": ["Mei"]},
        True,
        None,
        TEMPLATE_PARAMS,
        True,
    ),
    (
        "too-many-params",
        [_contact(), _inbound()],
        {"params": ["Mei", "Acuven", "extra"]},
        True,
        None,
        TEMPLATE_PARAMS,
        True,
    ),
    (
        "blank-param",
        [_contact(), _inbound()],
        {"params": ["Mei", "   "]},
        True,
        None,
        TEMPLATE_PARAMS,
        True,
    ),
    (
        "param-too-long",
        [_contact(), _inbound()],
        {"params": ["M" * 11, "Acuven"]},
        True,
        None,
        TEMPLATE_PARAMS,
        True,
    ),
]


@pytest.mark.parametrize(
    ("rows", "kwargs", "configured", "templates", "expected", "reads_whitelist"),
    [case[1:] for case in REJECTIONS],
    ids=[case[0] for case in REJECTIONS],
)
async def test_each_send_check_stores_nothing_and_does_not_call_send_template(
    async_session_maker, monkeypatch, rows, kwargs, configured, templates, expected, reads_whitelist
):
    """Guards AC2 (each check in design section 15 item 8 returns its own result, stores
    no message, does not call send-template and leaves no claim); NO_OPT_IN is design
    section 12 example 9."""
    await _seed(async_session_maker, *rows)
    seeded = [message.id for message in await _outbound(async_session_maker)]
    if configured:
        _configured(monkeypatch)
    else:
        monkeypatch.setattr(settings, "chatbot_send_url", "")
        monkeypatch.setattr(settings, "chatbot_send_secret", "")
    calls = _chatbot(monkeypatch, templates=templates)

    result = await _send(async_session_maker, **kwargs)

    assert result.category == expected
    assert result.message is None
    assert [message.id for message in await _outbound(async_session_maker)] == seeded
    assert _paths(calls) == ([TEMPLATES_PATH] if reads_whitelist else [])
    if rows and expected != CLAIMED:
        assert await _stored_claim(async_session_maker) == (None, None)


async def test_template_pending_counts_a_same_second_reply_as_no_reply(
    async_session_maker, monkeypatch
):
    """Guards AC2 (an inbound at the same time as the newest template is not a reply)."""
    sent_at = NOW - timedelta(hours=1)
    await _seed(
        async_session_maker,
        _contact(),
        _template_message(created_at=sent_at),
        _inbound(sent_at),
    )
    _configured(monkeypatch)
    calls = _chatbot(monkeypatch)
    assert (await _send(async_session_maker)).category == TEMPLATE_PENDING
    assert calls == []


async def test_template_pending_ends_when_the_visitor_writes_again(
    async_session_maker, monkeypatch
):
    """Guards AC2 (a WhatsApp inbound strictly later than the newest template lifts
    TEMPLATE_PENDING, even when an older template was never confirmed)."""
    await _seed(
        async_session_maker,
        _contact(),
        _inbound(NOW - timedelta(days=3)),
        _template_message(created_at=NOW - timedelta(days=2, hours=1)),
        _inbound(NOW - timedelta(days=2), message_id="inbound-2"),
    )
    _configured(monkeypatch)
    calls = _chatbot(monkeypatch, send=_sent())

    assert (await _send(async_session_maker)).category == SENT
    assert _paths(calls) == [TEMPLATES_PATH, SEND_TEMPLATE_PATH]


async def test_claim_taken_while_reading_the_whitelist_refuses_the_send(
    async_session_maker, monkeypatch
):
    """Guards AC3 (once every check passed, the conditional claim matching no row returns
    CLAIMED, stores nothing and does not call send-template)."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    until = NOW + timedelta(minutes=15)

    async def m1_claims_meanwhile():
        async with async_session_maker() as other:
            await other.execute(
                update(Contact)
                .where(Contact.id == "c1")
                .values(reply_claimed_by=M1, reply_claimed_until=until)
            )
            await other.commit()
        return _templates_ok()

    calls = _chatbot(monkeypatch, templates=m1_claims_meanwhile, send=_sent())
    result = await _send(async_session_maker)

    assert (result.category, result.claimed_by, result.claimed_until) == (CLAIMED, M1, until)
    assert await _outbound(async_session_maker) == []
    assert _paths(calls) == [TEMPLATES_PATH]


# --- Successful send, stored row and request (AC3, AC4) -------------------------


async def test_send_template_example_8(async_session_maker, monkeypatch):
    """Guards AC3 and AC4; design section 12 example 8: the stored row (three template
    columns with trimmed params, rendered body, fields as for a text send) and the
    claim are committed before the signed send-template call with the exact request
    body; a sent response fills external_id and sent, starts the handover, and its
    body does not replace the stored one. A second template then is TEMPLATE_PENDING."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    rendered = "Hi Mei, Acuven would like to follow up. Mei, reply any time."
    at_call = {}

    async def chatbot_sends():
        # Recorded and asserted afterwards: the service swallows assertion errors here.
        at_call["rows"] = [
            (message.id, message.external_id, message.delivery_status)
            for message in await _outbound(async_session_maker)
        ]
        at_call["claim"] = await _stored_claim(async_session_maker)
        return _sent(
            body="chatbot rendered text",
            handover={
                "active": True,
                "occurred_at": "2026-11-01T10:00:05Z",
                "expires_at": "2026-11-02T10:00:05Z",
            },
        )

    calls = _chatbot(monkeypatch, send=chatbot_sends)
    result = await _send(async_session_maker, params=["  Mei ", "\tAcuven\n"])

    assert result.category == SENT
    [stored] = await _outbound(async_session_maker)
    assert result.message.id == stored.id
    assert at_call == {
        "rows": [(stored.id, None, None)],
        "claim": (S1, NOW + timedelta(minutes=15)),
    }
    assert (
        stored.channel,
        stored.direction,
        stored.author_kind,
        stored.sent_by,
        stored.assigned_to,
        stored.sender_id,
        stored.recipient_id,
        stored.is_read,
    ) == ("whatsapp", "outbound", "agent", S1, S1, "ai_chatbot_demo", PHONE, True)
    assert (stored.template_name, stored.template_language, stored.template_params) == (
        "crm_followup",
        "en",
        ["Mei", "Acuven"],
    )
    assert stored.body == rendered
    assert (stored.external_id, stored.delivery_status) == ("wamid.T1", "sent")
    assert await _stored_claim(async_session_maker) == (S1, NOW + timedelta(minutes=15))

    assert _paths(calls) == [TEMPLATES_PATH, SEND_TEMPLATE_PATH]
    assert calls[1][1] == {
        "request_id": stored.id,
        "visitor": {"phone": PHONE, "bsuid": BSUID},
        "template": {"name": "crm_followup", "language": "en", "params": ["Mei", "Acuven"]},
        "agent": {"id": S1, "name": "Mei"},
    }
    async with async_session_maker() as session:
        contact = await session.get(Contact, "c1")
    assert contact.wa_handover_active is True

    again = await _send(async_session_maker, now=NOW + timedelta(minutes=1))
    assert again.category == TEMPLATE_PENDING
    assert len(await _outbound(async_session_maker)) == 1


async def test_send_template_without_params_uses_the_body_as_is(async_session_maker, monkeypatch):
    """Guards AC3 (a whitelist entry without params renders to its body unchanged)."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    calls = _chatbot(monkeypatch, send=_sent())

    result = await _send(async_session_maker, name="crm_hello", params=[])

    assert result.category == SENT
    assert (result.message.body, result.message.template_params) == ("Hello", [])
    assert calls[1][1]["template"] == {"name": "crm_hello", "language": "en", "params": []}


async def test_explicit_failure_deletes_the_stored_template(async_session_maker, monkeypatch):
    """Guards AC4 (a response proving chatbot did not send deletes the row and returns
    SEND_FAILED with chatbot's error, as for text sends)."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    _chatbot(
        monkeypatch,
        send=_response(
            502, {"result": "rejected", "error_code": 132001, "error_title": "Template missing"}
        ),
    )

    result = await _send(async_session_maker)

    assert (result.category, result.error_code, result.error_title) == (
        SEND_FAILED,
        132001,
        "Template missing",
    )
    assert await _outbound(async_session_maker) == []


@pytest.mark.parametrize(
    "outcome",
    [_response(500), _response(200, {"result": "sent"}), httpx.ReadTimeout("slow")],
    ids=["500", "sent-without-id", "timeout"],
)
async def test_unknown_outcome_keeps_the_stored_template(async_session_maker, monkeypatch, outcome):
    """Guards AC4 (an outcome that is neither sent nor an explicit failure keeps the row
    unconfirmed with its template columns)."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    _chatbot(monkeypatch, send=outcome)

    result = await _send(async_session_maker)

    assert result.category == UNCONFIRMED
    [stored] = await _outbound(async_session_maker)
    assert result.message.id == stored.id
    assert (stored.external_id, stored.delivery_status, stored.template_name) == (
        None,
        None,
        "crm_followup",
    )


# --- Retry (AC5) ----------------------------------------------------------------


async def test_template_retry_with_the_window_closed_calls_send_template(
    async_session_maker, monkeypatch
):
    """Guards AC5 (a template message's retry skips the window, eligibility, rate and
    whitelist checks and calls send-template with the same request_id, the stored
    name, language and params, the stored recipient and the original sender)."""
    await _seed(
        async_session_maker,
        _user(S1, "Mei"),
        _contact(),
        _inbound(NOW - timedelta(days=2)),
        _template_message(recipient_id="60111111111", template_params=["Mei", "Acuven Sdn"]),
    )
    _configured(monkeypatch)
    calls = _chatbot(monkeypatch, send=_sent(body="chatbot rendered text"))

    result = await _retry(async_session_maker, requester_id=M1)

    assert result.category == SENT
    assert _paths(calls) == [SEND_TEMPLATE_PATH]
    assert calls[0][1] == {
        "request_id": "msg1",
        "visitor": {"phone": "60111111111", "bsuid": BSUID},
        "template": {"name": "crm_followup", "language": "en", "params": ["Mei", "Acuven Sdn"]},
        "agent": {"id": S1, "name": "Mei"},
    }
    async with async_session_maker() as session:
        stored = await session.get(Message, "msg1")
    assert (stored.external_id, stored.delivery_status) == ("wamid.T1", "sent")
    assert stored.body == _template_message().body
    assert await _stored_claim(async_session_maker) == (M1, NOW + timedelta(minutes=15))


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [(_response(502, {"result": "rejected"}), SEND_FAILED), (_response(500), UNCONFIRMED)],
    ids=["explicit-failure", "unknown"],
)
async def test_template_retry_keeps_the_row_whatever_the_outcome(
    async_session_maker, monkeypatch, outcome, expected
):
    """Guards AC5 (after the call a template retry is handled like a text retry: the
    row is kept)."""
    await _seed(async_session_maker, _contact(), _inbound(), _template_message())
    _configured(monkeypatch)
    _chatbot(monkeypatch, send=outcome)

    result = await _retry(async_session_maker)

    assert result.category == expected
    assert [message.id for message in await _outbound(async_session_maker)] == ["msg1"]


@pytest.mark.parametrize(
    ("message_kwargs", "contact_kwargs", "configured", "expected"),
    [
        ({"external_id": "wamid.done"}, {}, True, SENT),
        ({"created_at": NOW - timedelta(seconds=10)}, {}, True, IN_PROGRESS),
        ({"created_at": NOW - timedelta(hours=24)}, {}, True, RETRY_EXPIRED),
        ({}, {"deleted_at": NOW}, True, NOT_FOUND),
        (
            {},
            {"reply_claimed_by": M1, "reply_claimed_until": NOW + timedelta(minutes=5)},
            True,
            CLAIMED,
        ),
        ({}, {}, False, NOT_CONFIGURED),
    ],
    ids=["confirmed", "in-progress", "expired", "deleted", "claimed", "not-configured"],
)
async def test_template_retry_keeps_the_other_retry_checks(
    async_session_maker, monkeypatch, message_kwargs, contact_kwargs, configured, expected
):
    """Guards AC5 (confirmed, IN_PROGRESS, RETRY_EXPIRED, NOT_FOUND, claim and settings
    checks still apply to a template retry and none of them calls chatbot)."""
    await _seed(
        async_session_maker,
        _contact(**contact_kwargs),
        _inbound(),
        _template_message(**message_kwargs),
    )
    if configured:
        _configured(monkeypatch)
    else:
        monkeypatch.setattr(settings, "chatbot_send_url", "")
    calls = _chatbot(monkeypatch)

    assert (await _retry(async_session_maker)).category == expected
    assert calls == []


async def test_text_retry_still_calls_the_text_send(async_session_maker, monkeypatch):
    """Guards AC5 (a text message's retry is unchanged: it still goes to /internal/crm/send
    with its text)."""
    await _seed(
        async_session_maker,
        _user(S1, "Mei"),
        _contact(),
        _inbound(NOW - timedelta(hours=1)),
        _template_message(
            body="plain reply", template_name=None, template_language=None, template_params=None
        ),
    )
    _configured(monkeypatch)
    calls = _chatbot(monkeypatch, send=_sent())

    assert (await _retry(async_session_maker)).category == SENT
    assert _paths(calls) == ["/internal/crm/send"]
    assert calls[0][1] == {
        "request_id": "msg1",
        "visitor": {"phone": PHONE, "bsuid": BSUID},
        "text": "plain reply",
        "agent": {"id": S1, "name": "Mei"},
    }
