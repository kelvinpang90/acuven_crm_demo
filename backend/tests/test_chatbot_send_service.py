"""CRM-TASK-013 business rules for replies sent through ai_chatbot_demo."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import chatbot_event_service, chatbot_send_service

PHONE = "60123456789"


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _messages(maker):
    async with maker() as session:
        return (await session.execute(select(Message).order_by(Message.id))).scalars().all()


async def _contact(maker, **kwargs):
    kwargs.setdefault("phone", PHONE)
    contact = Contact(id="c1", name="Visitor", **kwargs)
    await _seed(maker, contact)
    return contact


async def _inbound(maker, created_at=None):
    await _seed(
        maker,
        Message(
            id="inbound",
            contact_id="c1",
            channel="whatsapp",
            direction="inbound",
            sender_id=PHONE,
            recipient_id="ai_chatbot_demo",
            body="hello",
            is_read=False,
            created_at=created_at or datetime.utcnow(),
        ),
    )


async def _call(maker):
    async with maker() as session:
        return await chatbot_send_service.send_chatbot_reply(
            session, contact_id="c1", text="reply", agent_id="u1", agent_name="Agent One"
        )


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_send_url", "http://chatbot.internal:8000")
    monkeypatch.setattr(settings, "chatbot_send_secret", "send-secret")


def _stub_http(monkeypatch, response=None, exception=None):
    post = AsyncMock(side_effect=exception) if exception else AsyncMock(return_value=response)
    client = MagicMock()
    client.return_value.__aenter__.return_value.post = post
    monkeypatch.setattr(
        chatbot_send_service,
        "httpx",
        SimpleNamespace(
            AsyncClient=client,
            ConnectError=httpx.ConnectError,
            ConnectTimeout=httpx.ConnectTimeout,
            PoolTimeout=httpx.PoolTimeout,
            ReadTimeout=httpx.ReadTimeout,
            WriteTimeout=httpx.WriteTimeout,
            RemoteProtocolError=httpx.RemoteProtocolError,
            HTTPError=httpx.HTTPError,
            Timeout=httpx.Timeout,
        ),
    )
    return post


def _response(status, data=None):
    result = MagicMock(status_code=status)
    result.json.return_value = data
    return result


async def test_signature_body_and_store_before_call(async_session_maker, monkeypatch):
    """AC1/AC3: sign exact UTF-8 bytes and commit the unconfirmed row before POST."""
    _configure(monkeypatch)
    await _contact(async_session_maker, whatsapp_bsuid="MY.visitor")
    await _inbound(async_session_maker)
    chatbot_response = _response(200, {"result": "sent", "wa_message_id": "wa.1", "handover": None})
    post = _stub_http(monkeypatch, chatbot_response)

    async def assert_row_is_committed(*_args, **_kwargs):
        outbound = [
            row for row in await _messages(async_session_maker) if row.direction == "outbound"
        ]
        assert len(outbound) == 1 and outbound[0].external_id is None
        return chatbot_response

    post.side_effect = assert_row_is_committed

    result = await _call(async_session_maker)
    assert result["result"] == "sent"
    kwargs = post.call_args.kwargs
    payload = json.loads(kwargs["content"])
    assert payload["visitor"] == {"phone": PHONE, "bsuid": "MY.visitor"}
    assert payload["agent"] == {"id": "u1", "name": "Agent One"}
    signed = kwargs["headers"]["X-Acuven-Timestamp"].encode() + b"." + kwargs["content"]
    expected = hmac.new(b"send-secret", signed, hashlib.sha256).hexdigest()
    assert kwargs["headers"]["X-Acuven-Signature"] == f"sha256={expected}"
    assert kwargs["timeout"].connect == 30.0
    rows = await _messages(async_session_maker)
    outbound = [row for row in rows if row.direction == "outbound"]
    assert len(rows) == 2 and outbound[0].external_id == "wa.1"


@pytest.mark.parametrize(
    ("contact_kwargs", "inbound_at", "configured", "expected"),
    [
        ({"deleted_at": datetime.utcnow()}, None, True, "not_found"),
        ({"phone": "bad"}, None, True, "no_phone"),
        ({}, None, True, "window_closed"),
        ({}, datetime.utcnow() - timedelta(hours=24), True, "window_closed"),
        ({}, datetime.utcnow(), False, "not_configured"),
    ],
)
async def test_validations_do_not_store_or_call(
    async_session_maker, monkeypatch, contact_kwargs, inbound_at, configured, expected
):
    """AC2: missing/deleted, phone, inclusive 24h window and config reject before POST."""
    await _contact(async_session_maker, **contact_kwargs)
    if inbound_at:
        await _inbound(async_session_maker, inbound_at)
    if configured:
        _configure(monkeypatch)
    post = _stub_http(monkeypatch, _response(200, {}))
    result = await _call(async_session_maker)
    assert result["result"] == expected
    if expected == "window_closed":
        assert result["closes_at"] is None or result["closes_at"] <= datetime.utcnow()
    assert len(await _messages(async_session_maker)) == (1 if inbound_at else 0)
    post.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "data", "expected", "rows"),
    [
        (502, {"result": "rejected", "error_code": 131047, "error_title": "Denied"}, "failed", 1),
        (401, {}, "failed", 1),
        (422, {}, "failed", 1),
        (503, {}, "failed", 1),
        (409, {"result": "in_progress"}, "unconfirmed", 2),
        (504, {"result": "unknown"}, "unconfirmed", 2),
        (200, {"result": "sent"}, "unconfirmed", 2),
        (
            200,
            {"result": "duplicate", "wa_message_id": "wa.duplicate", "handover": None},
            "sent",
            2,
        ),
    ],
)
async def test_response_classes_delete_or_keep(
    async_session_maker, monkeypatch, status, data, expected, rows
):
    """AC4: only definite non-send deletes; all ambiguous protocol results remain."""
    _configure(monkeypatch)
    await _contact(async_session_maker)
    await _inbound(async_session_maker)
    _stub_http(monkeypatch, _response(status, data))
    result = await _call(async_session_maker)
    assert result["result"] == expected
    saved = await _messages(async_session_maker)
    assert len(saved) == rows
    if expected == "failed":
        assert result["error"] == (
            {"error_code": 131047, "error_title": "Denied"} if status == 502 else None
        )
    elif expected == "unconfirmed":
        assert [row for row in saved if row.direction == "outbound"][0].external_id is None
    else:
        assert [row for row in saved if row.direction == "outbound"][
            0
        ].external_id == "wa.duplicate"


@pytest.mark.parametrize(
    "exception",
    [
        httpx.ReadTimeout("slow read"),
        httpx.WriteTimeout("slow write"),
        httpx.RemoteProtocolError("cut off"),
    ],
)
async def test_ambiguous_transport_failures_keep_message(
    async_session_maker, monkeypatch, exception
):
    """AC4: read/write deadlines and a mid-stream disconnect leave an unconfirmed row."""
    _configure(monkeypatch)
    await _contact(async_session_maker)
    await _inbound(async_session_maker)
    _stub_http(monkeypatch, exception=exception)
    assert (await _call(async_session_maker))["result"] == "unconfirmed"
    assert len(await _messages(async_session_maker)) == 2


@pytest.mark.parametrize(
    "exception",
    [httpx.ConnectError("no route"), httpx.ConnectTimeout("slow"), httpx.PoolTimeout("busy")],
)
async def test_unsent_transport_failures_delete(async_session_maker, monkeypatch, exception):
    """AC4: connection establishment, connect timeout and pool timeout are definite non-sends."""
    _configure(monkeypatch)
    await _contact(async_session_maker)
    await _inbound(async_session_maker)
    _stub_http(monkeypatch, exception=exception)
    assert (await _call(async_session_maker))["result"] == "failed"
    assert len(await _messages(async_session_maker)) == 1


async def test_conditional_updates_preserve_delivery_and_response_handover_is_strict(
    async_session_maker, monkeypatch
):
    """AC5: sent never downgrades delivered; equal-second response handover loses to event state."""
    _configure(monkeypatch)
    occurred = datetime(2026, 10, 13, 1, 2, 3, tzinfo=timezone.utc)
    await _contact(
        async_session_maker,
        wa_handover_active=False,
        wa_handover_updated_at=occurred.replace(tzinfo=None),
    )
    await _inbound(async_session_maker)
    response = _response(
        200,
        {
            "result": "sent",
            "wa_message_id": "wa.conditional",
            "handover": {
                "active": True,
                "expires_at": "2026-10-14T01:02:03Z",
                "occurred_at": "2026-10-13T01:02:03Z",
            },
        },
    )

    async def event_arrives_before_response(_payload):
        """The agent_reply/receipt writer wins the race before this response returns."""
        async with async_session_maker() as event_session:
            outbound = (
                await event_session.execute(select(Message).where(Message.direction == "outbound"))
            ).scalar_one()
            outbound.external_id = "wa.from-event"
            outbound.delivery_status = "delivered"
            await event_session.commit()
        return "sent", response.json()

    monkeypatch.setattr(chatbot_send_service, "_post_send", event_arrives_before_response)
    result = await _call(async_session_maker)
    assert result["message"].external_id == "wa.from-event"
    assert result["message"].delivery_status == "delivered"
    async with async_session_maker() as session:
        contact = (await session.execute(select(Contact).where(Contact.id == "c1"))).scalar_one()
        assert contact.wa_handover_active is False
    # The function itself is strict too: a later response is the only one that writes.
    async with async_session_maker() as session:
        assert (
            await chatbot_event_service.write_response_handover(
                session,
                contact_id="c1",
                active=True,
                expires_at=occurred + timedelta(hours=1),
                occurred_at=occurred + timedelta(seconds=1),
            )
            == "stored"
        )
        await session.commit()
    async with async_session_maker() as session:
        contact = (await session.execute(select(Contact).where(Contact.id == "c1"))).scalar_one()
        assert contact.wa_handover_active is True
