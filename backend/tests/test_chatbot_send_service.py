"""CRM-TASK-013 business-rule tests for CRM -> chatbot agent replies."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import chatbot_send_service
from app.services.chatbot_send_service import (
    NO_PHONE,
    NOT_CONFIGURED,
    NOT_FOUND,
    SEND_FAILED,
    SENT,
    UNCONFIRMED,
    WINDOW_CLOSED,
    send_agent_reply,
)

T0 = datetime(2026, 10, 13, 8, 0, 0)
PHONE = "60123456789"


def _contact(**kwargs):
    kwargs.setdefault("phone", PHONE)
    kwargs.setdefault("assigned_to", "owner")
    return Contact(id="c1", name="Visitor", **kwargs)


def _inbound(created_at=T0):
    return Message(
        id="inbound-1",
        contact_id="c1",
        channel="whatsapp",
        direction="inbound",
        sender_id=PHONE,
        recipient_id="ai_chatbot_demo",
        body="hello",
        is_read=True,
        created_at=created_at,
    )


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _outbound(maker):
    async with maker() as session:
        return (
            await session.execute(
                select(Message).where(Message.direction == "outbound").order_by(Message.id)
            )
            .scalars()
            .all()
        )


def _response(status, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload if payload is not None else {}
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
    return client


def _configured(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_send_url", "http://chatbot.internal:8000")
    monkeypatch.setattr(settings, "chatbot_send_secret", "shared-send-secret")


async def _send(maker, **kwargs):
    async with maker() as session:
        result = await send_agent_reply(
            session,
            contact_id=kwargs.pop("contact_id", "c1"),
            text="reply",
            agent_id="u1",
            agent_name="Agent One",
            now=kwargs.pop("now", T0 + timedelta(hours=1)),
            **kwargs,
        )
    return result


@pytest.mark.parametrize(
    ("rows", "contact_id", "now", "url", "secret", "expected", "window"),
    [
        ([], "missing", T0, "url", "secret", NOT_FOUND, None),
        ([_contact(phone="bad")], "c1", T0, "url", "secret", NO_PHONE, None),
        ([_contact()], "c1", T0, "url", "secret", WINDOW_CLOSED, None),
        (
            [_contact(), _inbound(T0)],
            "c1",
            T0 + timedelta(hours=24),
            "url",
            "secret",
            WINDOW_CLOSED,
            T0 + timedelta(hours=24),
        ),
        (
            [_contact(), _inbound()],
            "c1",
            T0 + timedelta(hours=1),
            "",
            "secret",
            NOT_CONFIGURED,
            None,
        ),
        ([_contact(), _inbound()], "c1", T0 + timedelta(hours=1), "url", "", NOT_CONFIGURED, None),
    ],
)
async def test_pre_send_validation_does_not_persist_or_call(
    async_session_maker, monkeypatch, rows, contact_id, now, url, secret, expected, window
):
    """Guards AC2: each failed contact, phone, window, or configuration check stops before storage and HTTP."""
    await _seed(async_session_maker, *rows)
    monkeypatch.setattr(settings, "chatbot_send_url", url)
    monkeypatch.setattr(settings, "chatbot_send_secret", secret)
    post = AsyncMock()
    client = _stub_http(monkeypatch, post)

    result = await _send(async_session_maker, contact_id=contact_id, now=now)

    assert result.category == expected
    assert result.window_until == window
    assert await _outbound(async_session_maker) == []
    client.assert_not_called()
    post.assert_not_awaited()


async def test_sent_commits_before_call_signs_raw_body_and_writes_message(
    async_session_maker, monkeypatch
):
    """Guards AC1 and AC3: signed payload contains the prescribed fields and the unconfirmed row exists before HTTP."""
    await _seed(async_session_maker, _contact(whatsapp_bsuid="MY.visitor"), _inbound())
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        assert len(await _outbound(async_session_maker)) == 1
        body = json.loads(content)
        assert url == "http://chatbot.internal:8000/internal/crm/send"
        assert body["visitor"] == {"phone": PHONE, "bsuid": "MY.visitor"}
        assert body["agent"] == {"id": "u1", "name": "Agent One"}
        assert body["text"] == "reply"
        expected = hmac.new(
            b"shared-send-secret",
            headers["X-Acuven-Timestamp"].encode() + b"." + content,
            hashlib.sha256,
        ).hexdigest()
        assert headers["X-Acuven-Signature"] == "sha256=" + expected
        return _response(200, {"result": "sent", "wa_message_id": "wamid.1", "handover": None})

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    result = await _send(async_session_maker)

    assert result.category == SENT
    assert result.message.external_id == "wamid.1"
    assert result.message.delivery_status == "sent"
    assert result.message.sender_id == "ai_chatbot_demo"
    assert result.message.recipient_id == PHONE
    assert result.message.author_kind == "agent"
    assert result.message.sent_by == "u1"
    assert result.message.assigned_to == "owner"
    assert result.message.is_read is True


@pytest.mark.parametrize(
    "failure",
    [
        _response(502, {"result": "rejected", "error_code": "131026", "error_title": "Rejected"}),
        _response(401),
        _response(422),
        _response(503),
        httpx.ConnectError("no route"),
        httpx.ConnectTimeout("not connected"),
        httpx.PoolTimeout("no connection"),
    ],
)
async def test_explicit_failure_deletes_unconfirmed_row(async_session_maker, monkeypatch, failure):
    """Guards AC4: rejected, listed HTTP failures, and pre-send connection failures delete the row."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    _stub_http(monkeypatch, AsyncMock(side_effect=failure))

    result = await _send(async_session_maker)

    assert result.category == SEND_FAILED
    assert await _outbound(async_session_maker) == []


@pytest.mark.parametrize(
    "response_or_error",
    [
        _response(409, {"result": "in_progress"}),
        _response(504, {"result": "unknown"}),
        _response(500),
        _response(200, {"result": "sent"}),
        httpx.ReadTimeout("reply unknown"),
        httpx.ReadError("connection dropped"),
    ],
)
async def test_unknown_result_keeps_unconfirmed_row(
    async_session_maker, monkeypatch, response_or_error
):
    """Guards AC4: every non-explicit, non-confirmed outcome remains retriable and unconfirmed."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    _stub_http(monkeypatch, AsyncMock(side_effect=response_or_error))

    result = await _send(async_session_maker)

    assert result.category == UNCONFIRMED
    assert result.message.external_id is None
    assert result.message.delivery_status is None
    assert len(await _outbound(async_session_maker)) == 1


async def test_conditional_updates_do_not_downgrade_delivery_and_response_handover_is_strict(
    async_session_maker, monkeypatch
):
    """Guards AC5: callback-written delivery survives the response, while a same-second response handover loses to an event."""
    await _seed(
        async_session_maker,
        _contact(
            wa_handover_active=False,
            wa_handover_updated_at=T0 + timedelta(minutes=2),
        ),
        _inbound(),
    )
    _configured(monkeypatch)

    async def post(*args, **kwargs):
        request_id = json.loads(kwargs["content"])["request_id"]
        async with async_session_maker() as session:
            await session.execute(
                update(Message)
                .where(Message.id == request_id)
                .values(external_id="wamid.callback", delivery_status="delivered")
            )
            await session.commit()
        return _response(
            200,
            {
                "result": "sent",
                "wa_message_id": "wamid.response",
                "handover": {
                    "active": True,
                    "expires_at": (T0 + timedelta(hours=25))
                    .replace(tzinfo=timezone.utc)
                    .isoformat(),
                    "occurred_at": (T0 + timedelta(minutes=2))
                    .replace(tzinfo=timezone.utc)
                    .isoformat(),
                },
            },
        )

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    result = await _send(async_session_maker)

    assert result.category == SENT
    assert result.message.external_id == "wamid.callback"
    assert result.message.delivery_status == "delivered"
    async with async_session_maker() as session:
        contact = (await session.execute(select(Contact).where(Contact.id == "c1"))).scalar_one()
    assert contact.wa_handover_active is False
    assert contact.wa_handover_updated_at == T0 + timedelta(minutes=2)
