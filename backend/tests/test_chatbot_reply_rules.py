"""CRM-TASK-014 rules for retry callbacks and returning a visitor to AI."""

import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import chatbot_event_service, chatbot_send_service
from app.services.chatbot_send_service import (
    ENDED,
    HANDOFF_FAILED,
    IN_PROGRESS,
    NO_IDENTITY,
    NOT_ACTIVE,
    NOT_CONFIGURED,
    NOT_FOUND,
    NOT_RETRYABLE,
    RETRY_EXPIRED,
    SEND_FAILED,
    SENT,
    UNCONFIRMED,
    WINDOW_CLOSED,
    handback_to_ai,
    retry_agent_reply,
)

T0 = datetime(2026, 10, 14, 8, 0)
PHONE = "60123456789"


def _contact(**kwargs):
    values = {"id": "c1", "name": "Visitor", "phone": PHONE}
    values.update(kwargs)
    return Contact(**values)


def _agent_message(**kwargs):
    values = {
        "id": "m1",
        "contact_id": "c1",
        "channel": "whatsapp",
        "direction": "outbound",
        "sender_id": "ai_chatbot_demo",
        "recipient_id": PHONE,
        "body": "original reply",
        "is_read": True,
        "author_kind": "agent",
        "sent_by": "deleted-user",
        "created_at": T0,
    }
    values.update(kwargs)
    return Message(**values)


def _inbound():
    return Message(
        id="inbound",
        contact_id="c1",
        channel="whatsapp",
        direction="inbound",
        sender_id=PHONE,
        recipient_id="ai_chatbot_demo",
        body="hello",
        is_read=True,
        created_at=T0,
    )


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


def _configured(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_send_url", "http://chatbot.internal:8000")
    monkeypatch.setattr(settings, "chatbot_send_secret", "secret")


def _unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_send_url", "")
    monkeypatch.setattr(settings, "chatbot_send_secret", "")


def _response(status, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload or {}
    return response


def _stub_http(monkeypatch, post):
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
    return post


async def _retry(maker, **kwargs):
    async with maker() as session:
        return await retry_agent_reply(
            session, message_id="m1", now=kwargs.pop("now", T0 + timedelta(minutes=1))
        )


@pytest.mark.parametrize(
    ("message", "now", "category"),
    [
        (_agent_message(author_kind="console"), T0 + timedelta(minutes=1), NOT_RETRYABLE),
        (_agent_message(external_id="wamid.done"), T0 + timedelta(minutes=1), SENT),
        (_agent_message(), T0 + timedelta(seconds=44), IN_PROGRESS),
        (_agent_message(), T0 + timedelta(hours=24), RETRY_EXPIRED),
    ],
)
async def test_retry_preconditions_do_not_call_chatbot(
    async_session_maker, monkeypatch, message, now, category
):
    """Guards retry acceptance rules (1)--(3): local state wins before a call."""
    await _seed(
        async_session_maker,
        _contact(deleted_at=T0) if category in (IN_PROGRESS, RETRY_EXPIRED) else _contact(),
        message,
    )
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock())
    result = await _retry(async_session_maker, now=now)
    assert result.category == category
    post.assert_not_awaited()


@pytest.mark.parametrize("contact", [None, _contact(deleted_at=T0)])
async def test_retry_missing_or_deleted_contact_does_not_call(
    async_session_maker, monkeypatch, contact
):
    """Guards retry acceptance rule (4): deleted contacts cannot be retried."""
    await _seed(async_session_maker, *([contact] if contact else []), _agent_message())
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock())
    assert (await _retry(async_session_maker)).category == NOT_FOUND
    post.assert_not_awaited()


@pytest.mark.parametrize(
    "configured, inbound_at, expected",
    [
        (True, T0 - timedelta(days=2), WINDOW_CLOSED),
        (False, T0, NOT_CONFIGURED),
    ],
)
async def test_retry_window_and_configuration_do_not_call(
    async_session_maker, monkeypatch, configured, inbound_at, expected
):
    """Guards retry acceptance rule (5): window and settings are checked locally."""
    await _seed(async_session_maker, _contact(), _agent_message(), _inbound())
    async with async_session_maker() as session:
        inbound = (
            await session.execute(select(Message).where(Message.id == "inbound"))
        ).scalar_one()
        inbound.created_at = inbound_at
        await session.commit()
    if configured:
        _configured(monkeypatch)
    else:
        _unconfigured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock())
    assert (await _retry(async_session_maker)).category == expected
    post.assert_not_awaited()


async def test_retry_reuses_durable_request_details_and_confirms(async_session_maker, monkeypatch):
    """Guards retry acceptance rules (6)--(7); design section 11 example 4 (duplicate confirms).

    The original recipient and idempotency key survive later contact changes.
    """
    await _seed(
        async_session_maker,
        _contact(phone="60111111111", whatsapp_bsuid="new.bsuid"),
        _agent_message(),
        _inbound(),
    )
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        body = json.loads(content)
        assert url.endswith("/internal/crm/send")
        assert body == {
            "request_id": "m1",
            "visitor": {"phone": PHONE, "bsuid": "new.bsuid"},
            "text": "original reply",
            "agent": {"id": "deleted-user", "name": "deleted-user"},
        }
        return _response(200, {"result": "duplicate", "wa_message_id": "wamid.1"})

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    result = await _retry(async_session_maker)
    assert result.category == SENT
    assert result.message.external_id == "wamid.1"
    assert result.message.delivery_status == "sent"


async def test_retry_sends_null_bsuid_when_contact_has_none(async_session_maker, monkeypatch):
    """Guards retry acceptance rule (6): an absent current BSUID is sent as null."""
    await _seed(async_session_maker, _contact(), _agent_message(), _inbound())
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        assert json.loads(content)["visitor"] == {"phone": PHONE, "bsuid": None}
        return _response(200, {"result": "duplicate", "wa_message_id": "wamid.1"})

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    assert (await _retry(async_session_maker)).category == SENT


@pytest.mark.parametrize(
    ("outcome", "category"),
    [
        (_response(502, {"result": "rejected", "error_code": 131026}), SEND_FAILED),
        (_response(401), SEND_FAILED),
        (httpx.ReadTimeout("late"), UNCONFIRMED),
        (httpx.ConnectError("offline"), SEND_FAILED),
        (_response(200, {"result": "mystery"}), UNCONFIRMED),
    ],
)
async def test_retry_failures_keep_the_original_message(
    async_session_maker, monkeypatch, outcome, category
):
    """Guards retry acceptance rule (8); design section 11 example 4 (rejected or 401 retry).

    Every unsuccessful retry keeps the message unchanged and reports the same
    category CRM-TASK-013 would: explicit failures as send_failed, the rest as
    unconfirmed.
    """
    await _seed(async_session_maker, _contact(), _agent_message(), _inbound())
    _configured(monkeypatch)
    _stub_http(monkeypatch, AsyncMock(side_effect=[outcome]))
    result = await _retry(async_session_maker)
    assert result.category == category
    assert result.message.id == "m1"
    async with async_session_maker() as session:
        message = (await session.execute(select(Message).where(Message.id == "m1"))).scalar_one()
    assert message.external_id is None and message.delivery_status is None


@pytest.mark.parametrize(
    "message, wa_id, expected",
    [
        (None, "wamid.1", "ignored"),
        (_agent_message(author_kind="ai"), "wamid.1", "ignored"),
        (_agent_message(external_id="wamid.1"), "wamid.1", "duplicate"),
    ],
)
async def test_agent_reply_ignores_non_agent_and_deduplicates(
    async_session_maker, message, wa_id, expected
):
    """Guards agent_reply acceptance rules (9)--(10); design section 11 examples 9 and 4.

    A missing message is ignored (example 9); a callback after the retry already
    confirmed the same id is a duplicate (example 4).
    """
    await _seed(async_session_maker, *([message] if message else []))
    async with async_session_maker() as session:
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="m1", wa_message_id=wa_id, body="event"
            )
            == expected
        )


@pytest.mark.parametrize(
    ("delivery_status", "expected_status"),
    [(None, "sent"), ("delivered", "delivered")],
)
async def test_agent_reply_stores_without_overwriting_status(
    async_session_maker, delivery_status, expected_status
):
    """Guards agent_reply acceptance rule (11); design section 11 example 3 (event part).

    Confirmation fills only empty fields and never rewrites the body.
    """
    await _seed(async_session_maker, _agent_message(delivery_status=delivery_status))
    async with async_session_maker() as session:
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="m1", wa_message_id="wamid.1", body="changed"
            )
            == "stored"
        )
        message = (await session.execute(select(Message).where(Message.id == "m1"))).scalar_one()
        assert message.external_id == "wamid.1"
        assert message.delivery_status == expected_status
        assert message.body == "original reply"


async def test_agent_reply_duplicate_external_id_belongs_to_another_message(async_session_maker):
    """Guards agent_reply acceptance rule (10): another message owning the id wins."""
    await _seed(
        async_session_maker,
        _agent_message(),
        _agent_message(id="m2", external_id="wamid.1"),
    )
    async with async_session_maker() as session:
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="m1", wa_message_id="wamid.1", body="event"
            )
            == "duplicate"
        )
        assert (await session.get(Message, "m1")).external_id is None


async def test_agent_reply_conflict_warns_and_changes_nothing(async_session_maker, caplog):
    """Guards agent_reply acceptance rule (12): conflicting callback ids are warnings."""
    await _seed(async_session_maker, _agent_message(external_id="wamid.old"))
    async with async_session_maker() as session:
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="m1", wa_message_id="wamid.new", body="event"
            )
            == "ignored"
        )
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    async with async_session_maker() as session:
        assert (await session.get(Message, "m1")).external_id == "wamid.old"


async def _handback(maker, **kwargs):
    async with maker() as session:
        return await handback_to_ai(
            session, contact_id="c1", agent_id="u1", agent_name="Agent", **kwargs
        )


async def test_handback_uses_normalized_identity_and_writes_newer_state(
    async_session_maker, monkeypatch
):
    """Guards handback acceptance rules (1) and (4); design section 11 example 8.

    A valid ended response with a strictly newer time ends the takeover.
    """
    await _seed(
        async_session_maker,
        _contact(
            phone="012-345 6789",
            whatsapp_bsuid="visitor.bsuid",
            wa_handover_active=True,
            wa_handover_updated_at=T0,
        ),
    )
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        assert url.endswith("/internal/crm/handback")
        assert json.loads(content)["visitor"] == {"phone": PHONE, "bsuid": "visitor.bsuid"}
        return _response(
            200,
            {
                "result": "ended",
                "handover": {
                    "active": False,
                    "expires_at": None,
                    "occurred_at": "2026-10-14T08:01:00Z",
                },
            },
        )

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    result = await _handback(async_session_maker)
    assert result.category == ENDED
    assert result.contact.wa_handover_active is False
    async with async_session_maker() as session:
        contact = await session.get(Contact, "c1")
        assert contact.wa_handover_updated_at == T0 + timedelta(minutes=1)
        assert contact.wa_handover_active is False


@pytest.mark.parametrize(
    "contact, configured, expected",
    [
        (_contact(phone="bad"), True, NO_IDENTITY),
        (None, True, NOT_FOUND),
        (_contact(), False, NOT_CONFIGURED),
    ],
)
async def test_handback_local_failures_do_not_call(
    async_session_maker, monkeypatch, contact, configured, expected
):
    """Guards handback acceptance rule (3): identity, contact and settings failures do not call."""
    if contact:
        await _seed(async_session_maker, contact)
    if configured:
        _configured(monkeypatch)
    else:
        _unconfigured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock())
    assert (await _handback(async_session_maker)).category == expected
    post.assert_not_awaited()


async def test_handback_uses_bsuid_when_phone_is_not_normalizable(async_session_maker, monkeypatch):
    """Guards handback acceptance rule (2): a BSUID is enough when the phone is unusable."""
    await _seed(async_session_maker, _contact(phone="bad", whatsapp_bsuid="visitor.bsuid"))
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        assert json.loads(content)["visitor"] == {"phone": None, "bsuid": "visitor.bsuid"}
        return _response(200, {"result": "not_active", "handover": None})

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    assert (await _handback(async_session_maker)).category == NOT_ACTIVE


async def test_handback_same_second_and_not_active_do_not_change_state(
    async_session_maker, monkeypatch
):
    """Guards handback acceptance rules (4)--(5); design section 11 example 8 (second click).

    An ended response in the same second as the stored time and not_active do not write.
    """
    await _seed(async_session_maker, _contact(wa_handover_active=True, wa_handover_updated_at=T0))
    _configured(monkeypatch)
    _stub_http(
        monkeypatch,
        AsyncMock(
            return_value=_response(
                200,
                {
                    "result": "ended",
                    "handover": {
                        "active": False,
                        "expires_at": None,
                        "occurred_at": "2026-10-14T08:00:00Z",
                    },
                },
            )
        ),
    )
    assert (await _handback(async_session_maker)).category == ENDED
    _stub_http(
        monkeypatch,
        AsyncMock(return_value=_response(200, {"result": "not_active", "handover": None})),
    )
    assert (await _handback(async_session_maker)).category == NOT_ACTIVE
    async with async_session_maker() as session:
        contact = await session.get(Contact, "c1")
        assert contact.wa_handover_active is True
        assert contact.wa_handover_updated_at == T0


@pytest.mark.parametrize(
    "outcome",
    [
        _response(502),
        httpx.ReadTimeout("late"),
        httpx.ConnectError("offline"),
        _response(200, {"result": "unknown"}),
        _response(200, {"result": "ended"}),
        _response(
            200,
            {
                "result": "ended",
                "handover": {
                    "active": True,
                    "expires_at": None,
                    "occurred_at": "2026-10-14T08:02:00Z",
                },
            },
        ),
        _response(
            200,
            {
                "result": "ended",
                "handover": {
                    "active": False,
                    "expires_at": "2026-10-14T09:00:00Z",
                    "occurred_at": "2026-10-14T08:02:00Z",
                },
            },
        ),
    ],
)
async def test_handback_bad_or_failed_response_does_not_mutate(
    async_session_maker, monkeypatch, caplog, outcome
):
    """Guards handback acceptance rules (6)--(7): failures leave stored takeover unchanged."""
    await _seed(async_session_maker, _contact(wa_handover_active=True, wa_handover_updated_at=T0))
    _configured(monkeypatch)
    _stub_http(monkeypatch, AsyncMock(side_effect=[outcome]))
    assert (await _handback(async_session_maker)).category == HANDOFF_FAILED
    async with async_session_maker() as session:
        assert (await session.get(Contact, "c1")).wa_handover_updated_at == T0
    if getattr(outcome, "status_code", None) == 200:
        assert any(
            record.levelname == "ERROR"
            and "invalid chatbot handback response" in record.getMessage()
            for record in caplog.records
        )
