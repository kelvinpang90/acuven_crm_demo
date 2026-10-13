"""CRM-TASK-014 business-rule tests for retry, agent_reply, and handback."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
from sqlalchemy import select

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import chatbot_event_service, chatbot_send_service
from app.services.chatbot_send_service import (
    HANDBACK_ENDED,
    HANDBACK_FAILED,
    HANDBACK_NOT_ACTIVE,
    IN_PROGRESS,
    NO_IDENTITY,
    NOT_CONFIGURED,
    NOT_FOUND,
    NOT_RETRYABLE,
    RETRY_EXPIRED,
    SEND_FAILED,
    SENT,
    handback_to_ai,
    retry_agent_reply,
)

T0 = datetime(2026, 10, 14, 8, 0, 0)
PHONE = "60123456789"


def _contact(**kwargs):
    kwargs.setdefault("id", "c1")
    kwargs.setdefault("phone", PHONE)
    return Contact(name="Visitor", **kwargs)


def _message(**kwargs):
    kwargs.setdefault("id", "m1")
    kwargs.setdefault("contact_id", "c1")
    kwargs.setdefault("channel", "whatsapp")
    kwargs.setdefault("direction", "outbound")
    kwargs.setdefault("sender_id", "ai_chatbot_demo")
    kwargs.setdefault("recipient_id", PHONE)
    kwargs.setdefault("body", "reply")
    kwargs.setdefault("is_read", True)
    kwargs.setdefault("author_kind", "agent")
    kwargs.setdefault("sent_by", "former-agent")
    kwargs.setdefault("created_at", T0)
    return Message(**kwargs)


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


def _response(status, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload if payload is not None else {}
    return response


def _http(monkeypatch, post):
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


async def test_retry_guards_unretryable_confirmed_in_progress_and_expiry(
    async_session_maker, monkeypatch
):
    """Guards retry acceptance: only agents retry; confirmation and time guards precede HTTP."""
    await _seed(
        async_session_maker,
        _contact(),
        _message(id="other", author_kind="ai"),
        _message(id="sent", external_id="wamid.sent"),
        _message(id="young"),
        _message(id="old", created_at=T0 - timedelta(hours=24)),
    )
    post = AsyncMock()
    client = _http(monkeypatch, post)

    async with async_session_maker() as session:
        assert (
            await retry_agent_reply(session, message_id="other", now=T0)
        ).category == NOT_RETRYABLE
        confirmed = await retry_agent_reply(session, message_id="sent", now=T0)
        assert confirmed.category == SENT and confirmed.message.id == "sent"
        assert (
            await retry_agent_reply(session, message_id="young", now=T0 + timedelta(seconds=44))
        ).category == IN_PROGRESS
        assert (
            await retry_agent_reply(session, message_id="old", now=T0)
        ).category == RETRY_EXPIRED
    client.assert_not_called()
    post.assert_not_awaited()


async def test_retry_uses_saved_recipient_and_missing_sender_id_and_confirms(
    async_session_maker, monkeypatch
):
    """Guards retry acceptance: stable request/body/recipient and current BSUID are sent, then conditionally confirmed."""
    await _seed(
        async_session_maker,
        _contact(phone="60999999999", whatsapp_bsuid="bsuid-now"),
        _inbound(),
        _message(),
    )
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        assert url.endswith("/internal/crm/send")
        assert b'"request_id":"m1"' in content
        assert b'"phone":"60123456789"' in content
        assert b'"bsuid":"bsuid-now"' in content
        assert b'"name":"former-agent"' in content
        return _response(
            200, {"result": "duplicate", "wa_message_id": "wamid.retry", "handover": None}
        )

    _http(monkeypatch, AsyncMock(side_effect=post))
    async with async_session_maker() as session:
        result = await retry_agent_reply(session, message_id="m1", now=T0 + timedelta(minutes=1))
    assert result.category == SENT
    assert result.message.external_id == "wamid.retry"
    assert result.message.delivery_status == "sent"


async def test_retry_rejected_and_401_retain_the_original_message(async_session_maker, monkeypatch):
    """Guards design §11 example 4: rejected and 401 retries never delete an unconfirmed message."""
    await _seed(async_session_maker, _contact(), _inbound(), _message())
    _configured(monkeypatch)
    _http(
        monkeypatch,
        AsyncMock(
            side_effect=[
                _response(
                    502, {"result": "rejected", "error_code": 131047, "error_title": "closed"}
                ),
                _response(401),
            ]
        ),
    )
    async with async_session_maker() as session:
        rejected = await retry_agent_reply(session, message_id="m1", now=T0 + timedelta(minutes=1))
        unauthorized = await retry_agent_reply(
            session, message_id="m1", now=T0 + timedelta(minutes=2)
        )
    assert rejected.category == SEND_FAILED and rejected.error_code == 131047
    assert unauthorized.category == SEND_FAILED
    async with async_session_maker() as session:
        kept = (await session.execute(select(Message).where(Message.id == "m1"))).scalar_one()
    assert kept.external_id is None and kept.delivery_status is None


async def test_retry_missing_or_deleted_contact_does_not_call_chatbot(
    async_session_maker, monkeypatch
):
    """Guards retry acceptance: a missing or soft-deleted contact returns NOT_FOUND before HTTP."""
    await _seed(
        async_session_maker,
        _message(contact_id="gone"),
        _contact(id="deleted", deleted_at=T0),
        _message(id="deleted-message", contact_id="deleted"),
    )
    client = _http(monkeypatch, AsyncMock())
    async with async_session_maker() as session:
        assert (
            await retry_agent_reply(session, message_id="m1", now=T0 + timedelta(minutes=1))
        ).category == NOT_FOUND
        assert (
            await retry_agent_reply(
                session, message_id="deleted-message", now=T0 + timedelta(minutes=1)
            )
        ).category == NOT_FOUND
    client.assert_not_called()


async def test_agent_reply_stores_only_the_matching_agent_message(async_session_maker):
    """Guards agent_reply acceptance and design §11 example 3: it fills id/status without creating or changing content."""
    await _seed(
        async_session_maker,
        _contact(),
        _message(body="CRM body"),
        _message(id="non-agent", author_kind="ai"),
    )
    async with async_session_maker() as session:
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="missing", wa_message_id="wamid.x", body="event"
            )
            == chatbot_event_service.IGNORED
        )
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="non-agent", wa_message_id="wamid.x", body="event"
            )
            == chatbot_event_service.IGNORED
        )
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="m1", wa_message_id="wamid.x", body="event"
            )
            == chatbot_event_service.STORED
        )
        await session.commit()
    async with async_session_maker() as session:
        message = (await session.execute(select(Message).where(Message.id == "m1"))).scalar_one()
    assert (
        message.external_id == "wamid.x"
        and message.delivery_status == "sent"
        and message.body == "CRM body"
    )


async def test_agent_reply_duplicate_and_conflict_rules(async_session_maker):
    """Guards agent_reply acceptance: equal ids and occupied ids duplicate; a conflicting prior id is ignored."""
    await _seed(
        async_session_maker,
        _contact(),
        _message(external_id="wamid.same"),
        _message(id="occupied", external_id="wamid.occupied"),
        _message(id="conflict", external_id="wamid.other"),
        _message(id="empty"),
    )
    async with async_session_maker() as session:
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="m1", wa_message_id="wamid.same", body="x"
            )
            == chatbot_event_service.DUPLICATE
        )
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="empty", wa_message_id="wamid.occupied", body="x"
            )
            == chatbot_event_service.DUPLICATE
        )
        assert (
            await chatbot_event_service.store_agent_reply(
                session, crm_message_id="conflict", wa_message_id="wamid.new", body="x"
            )
            == chatbot_event_service.IGNORED
        )


async def test_handback_validates_before_http_and_returns_current_state(
    async_session_maker, monkeypatch
):
    """Guards handback acceptance: missing/deleted, no identity, and unconfigured contacts make no chatbot call."""
    await _seed(
        async_session_maker,
        _contact(),
        _contact(id="identity", phone="bad"),
        _contact(id="deleted", deleted_at=T0),
    )
    client = _http(monkeypatch, AsyncMock())
    async with async_session_maker() as session:
        assert (
            await handback_to_ai(session, contact_id="missing", agent_id="u1", agent_name="Agent")
        ).category == NOT_FOUND
        assert (
            await handback_to_ai(session, contact_id="deleted", agent_id="u1", agent_name="Agent")
        ).category == NOT_FOUND
        assert (
            await handback_to_ai(session, contact_id="identity", agent_id="u1", agent_name="Agent")
        ).category == NO_IDENTITY
        assert (
            await handback_to_ai(session, contact_id="c1", agent_id="u1", agent_name="Agent")
        ).category == NOT_CONFIGURED
    client.assert_not_called()


async def test_handback_ended_writes_strictly_newer_state_and_bad_responses_do_not_change_it(
    async_session_maker, monkeypatch, caplog
):
    """Guards design §11 examples 8/9: ended writes response handover; not_active and invalid ended keep state."""
    await _seed(
        async_session_maker,
        _contact(
            wa_handover_active=True,
            wa_handover_expires_at=T0 + timedelta(hours=1),
            wa_handover_updated_at=T0,
            whatsapp_bsuid="bsuid",
        ),
    )
    _configured(monkeypatch)
    ended_at = (T0 + timedelta(minutes=1)).replace(tzinfo=timezone.utc).isoformat()
    _http(
        monkeypatch,
        AsyncMock(
            side_effect=[
                _response(
                    200,
                    {
                        "result": "ended",
                        "handover": {"active": False, "expires_at": None, "occurred_at": ended_at},
                    },
                ),
                _response(200, {"result": "not_active", "handover": None}),
                _response(200, {"result": "ended", "handover": None}),
                _response(
                    200,
                    {
                        "result": "ended",
                        "handover": {
                            "active": True,
                            "expires_at": ended_at,
                            "occurred_at": ended_at,
                        },
                    },
                ),
                _response(200, {"result": "unexpected"}),
                _response(502, {"result": "unknown"}),
            ]
        ),
    )
    async with async_session_maker() as session:
        ended = await handback_to_ai(session, contact_id="c1", agent_id="u1", agent_name="Agent")
        inactive = await handback_to_ai(session, contact_id="c1", agent_id="u1", agent_name="Agent")
        invalid = await handback_to_ai(session, contact_id="c1", agent_id="u1", agent_name="Agent")
        equal_time = await handback_to_ai(
            session, contact_id="c1", agent_id="u1", agent_name="Agent"
        )
        unknown_result = await handback_to_ai(
            session, contact_id="c1", agent_id="u1", agent_name="Agent"
        )
        failed = await handback_to_ai(session, contact_id="c1", agent_id="u1", agent_name="Agent")
    assert (
        ended.category == HANDBACK_ENDED
        and ended.active is False
        and ended.updated_at == T0 + timedelta(minutes=1)
    )
    assert inactive.category == HANDBACK_NOT_ACTIVE and inactive.updated_at == ended.updated_at
    assert invalid.category == HANDBACK_FAILED and invalid.updated_at == ended.updated_at
    assert equal_time.category == HANDBACK_ENDED and equal_time.active is False
    assert (
        unknown_result.category == HANDBACK_FAILED and unknown_result.updated_at == ended.updated_at
    )
    assert failed.category == HANDBACK_FAILED and failed.updated_at == ended.updated_at
    assert any(
        record.levelname == "ERROR" and "invalid result" in record.message
        for record in caplog.records
    )
