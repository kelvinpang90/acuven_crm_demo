"""CRM-TASK-093: claims in WhatsApp send and retry, and reassignment clearing them.

Names follow docs/chatbot-phase3.md section 12: contact C1 (owner sales S1,
manager M1), now 10:00, last inbound 09:00. chatbot is stubbed at the HTTP client.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_send_service, claim_service, contact_service
from app.services.chatbot_send_service import (
    CLAIMED,
    SENT,
    WINDOW_CLOSED,
    retry_agent_reply,
    send_agent_reply,
)

NOW = datetime(2026, 10, 30, 10, 0)
PHONE = "60123456789"
S1 = "s1"
S2 = "s2"
M1 = "m1"


def _contact(**kwargs):
    values = {"id": "c1", "name": "Visitor", "phone": PHONE, "assigned_to": S1}
    values.update(kwargs)
    return Contact(**values)


def _inbound(created_at=NOW - timedelta(hours=1)):
    return Message(
        id="inbound",
        contact_id="c1",
        channel="whatsapp",
        direction="inbound",
        sender_id=PHONE,
        recipient_id="ai_chatbot_demo",
        body="hello",
        is_read=True,
        created_at=created_at,
    )


def _agent_message(**kwargs):
    values = {
        "id": "msg1",
        "contact_id": "c1",
        "channel": "whatsapp",
        "direction": "outbound",
        "sender_id": "ai_chatbot_demo",
        "recipient_id": PHONE,
        "body": "original reply",
        "is_read": True,
        "author_kind": "agent",
        "sent_by": S1,
        "created_at": NOW - timedelta(minutes=30),
    }
    values.update(kwargs)
    return Message(**values)


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _stored_claim(maker):
    async with maker() as session:
        contact = await session.get(Contact, "c1")
        return contact.reply_claimed_by, contact.reply_claimed_until


async def _outbound(maker):
    async with maker() as session:
        result = await session.execute(select(Message).where(Message.direction == "outbound"))
        return result.scalars().all()


def _configured(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_send_url", "http://chatbot.internal:8000")
    monkeypatch.setattr(settings, "chatbot_send_secret", "secret")


def _response(status, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload or {}
    return response


def _sent(wa_message_id="wamid.1"):
    # messages.external_id is unique, so each successful send needs its own id.
    return _response(200, {"result": "sent", "wa_message_id": wa_message_id})


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


async def _send(maker, agent_id, *, now=NOW):
    async with maker() as session:
        return await send_agent_reply(
            session,
            contact_id="c1",
            text="reply",
            agent_id=agent_id,
            agent_name=agent_id.upper(),
            now=now,
        )


async def _retry(maker, requester_id, *, now=NOW):
    async with maker() as session:
        return await retry_agent_reply(
            session, message_id="msg1", requester_id=requester_id, now=now
        )


async def test_send_while_another_user_holds_the_claim(async_session_maker, monkeypatch):
    """Guards AC3 (another user's valid claim: CLAIMED with holder and expiry, nothing
    stored, chatbot not called, claim unchanged); design section 12 example 2."""
    until = NOW + timedelta(minutes=15)
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=S1, reply_claimed_until=until),
        _inbound(),
    )
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock(return_value=_sent()))

    result = await _send(async_session_maker, M1, now=NOW + timedelta(minutes=5))

    assert (result.category, result.claimed_by, result.claimed_until) == (CLAIMED, S1, until)
    assert await _outbound(async_session_maker) == []
    post.assert_not_awaited()
    assert await _stored_claim(async_session_maker) == (S1, until)


async def test_claim_check_comes_before_the_phone_check(async_session_maker, monkeypatch):
    """Guards AC3 (the read-only claim check sits before NO_PHONE)."""
    until = NOW + timedelta(minutes=15)
    await _seed(
        async_session_maker,
        _contact(phone="bad", reply_claimed_by=S1, reply_claimed_until=until),
    )
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock())
    assert (await _send(async_session_maker, M1)).category == CLAIMED
    post.assert_not_awaited()


async def test_successful_send_claims_for_the_sender(async_session_maker, monkeypatch):
    """Guards AC3 (a send that passes every check claims for the sender until now + 15
    minutes, committed with the message, before chatbot is called); design section 12
    example 1."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        # The claim and the message are already committed when chatbot is called.
        assert await _stored_claim(async_session_maker) == (S1, NOW + timedelta(minutes=15))
        assert len(await _outbound(async_session_maker)) == 1
        return _sent()

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    result = await _send(async_session_maker, S1)

    assert result.category == SENT
    assert result.message.sent_by == S1
    assert await _stored_claim(async_session_maker) == (S1, NOW + timedelta(minutes=15))


async def test_send_renews_own_claim_and_takes_an_expired_one(async_session_maker, monkeypatch):
    """Guards AC3 (sending renews the sender's own claim and takes an expired one);
    design section 12 example 5."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=S1, reply_claimed_until=NOW + timedelta(minutes=15)),
        _inbound(),
    )
    _configured(monkeypatch)
    _stub_http(monkeypatch, AsyncMock(side_effect=[_sent("wamid.1"), _sent("wamid.2")]))

    assert (await _send(async_session_maker, S1, now=NOW + timedelta(minutes=5))).category == SENT
    assert await _stored_claim(async_session_maker) == (S1, NOW + timedelta(minutes=20))

    later = NOW + timedelta(minutes=21)
    assert (await _send(async_session_maker, M1, now=later)).category == SENT
    assert await _stored_claim(async_session_maker) == (M1, later + timedelta(minutes=15))


async def test_takeover_then_send_and_the_previous_holder_is_refused(
    async_session_maker, monkeypatch
):
    """Guards AC2 and AC3 together; design section 12 example 3 (M1 takes over and
    sends, S1's next send is refused with M1's claim)."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=S1, reply_claimed_until=NOW + timedelta(minutes=15)),
        _inbound(),
    )
    _configured(monkeypatch)
    _stub_http(monkeypatch, AsyncMock(return_value=_sent()))
    at_1005 = NOW + timedelta(minutes=5)

    async with async_session_maker() as session:
        taken = await claim_service.claim(
            session, contact_id="c1", user_id=M1, takeover=True, now=at_1005
        )
        await session.commit()
    assert taken.taken_over_from == S1

    sent = await _send(async_session_maker, M1, now=at_1005)
    assert sent.category == SENT
    assert sent.message.sent_by == M1
    assert await _stored_claim(async_session_maker) == (M1, NOW + timedelta(minutes=20))

    refused = await _send(async_session_maker, S1, now=NOW + timedelta(minutes=6))
    assert (refused.category, refused.claimed_by, refused.claimed_until) == (
        CLAIMED,
        M1,
        NOW + timedelta(minutes=20),
    )
    assert len(await _outbound(async_session_maker)) == 1


async def test_send_refused_by_closed_window_leaves_claim_unchanged(
    async_session_maker, monkeypatch
):
    """Guards AC3 (a send rejected by the window check does not claim)."""
    await _seed(async_session_maker, _contact(), _inbound(NOW - timedelta(days=2)))
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock())

    assert (await _send(async_session_maker, S1)).category == WINDOW_CLOSED
    assert await _stored_claim(async_session_maker) == (None, None)
    post.assert_not_awaited()


async def test_claim_taken_after_the_read_only_check_refuses_the_send(
    async_session_maker, monkeypatch
):
    """Guards AC3 (the conditional update matching no row returns CLAIMED, stores nothing
    and calls nothing); design section 12 example 4 (two agents send at once)."""
    await _seed(async_session_maker, _contact(), _inbound())
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock(return_value=_sent()))
    original = chatbot_send_service._last_inbound
    until = NOW + timedelta(minutes=15)

    async def m1_claims_first(db, contact_id):
        # Runs after the read-only claim check and before the conditional update.
        await db.execute(
            update(Contact)
            .where(Contact.id == contact_id)
            .values(reply_claimed_by=M1, reply_claimed_until=until)
        )
        return await original(db, contact_id)

    monkeypatch.setattr(chatbot_send_service, "_last_inbound", m1_claims_first)
    result = await _send(async_session_maker, S1)

    assert (result.category, result.claimed_by, result.claimed_until) == (CLAIMED, M1, until)
    assert await _outbound(async_session_maker) == []
    post.assert_not_awaited()


async def test_retry_while_another_user_holds_the_claim(async_session_maker, monkeypatch):
    """Guards AC4 (retry meeting another user's valid claim returns CLAIMED, does not call
    chatbot and leaves the message as it was)."""
    until = NOW + timedelta(minutes=10)
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=M1, reply_claimed_until=until),
        _agent_message(),
        _inbound(),
    )
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock(return_value=_sent()))

    result = await _retry(async_session_maker, S1)

    assert (result.category, result.claimed_by, result.claimed_until) == (CLAIMED, M1, until)
    post.assert_not_awaited()
    async with async_session_maker() as session:
        message = await session.get(Message, "msg1")
    assert (message.external_id, message.delivery_status, message.body) == (
        None,
        None,
        "original reply",
    )
    assert await _stored_claim(async_session_maker) == (M1, until)


async def test_retry_claims_for_the_requester_but_sends_as_the_original_agent(
    async_session_maker, monkeypatch
):
    """Guards AC4 (the claim follows the requester, chatbot's agent stays sent_by)."""
    await _seed(async_session_maker, _contact(), _agent_message(), _inbound())
    _configured(monkeypatch)

    async def post(url, *, content, headers):
        assert json.loads(content)["agent"]["id"] == S1
        assert await _stored_claim(async_session_maker) == (M1, NOW + timedelta(minutes=15))
        return _sent()

    _stub_http(monkeypatch, AsyncMock(side_effect=post))
    assert (await _retry(async_session_maker, M1)).category == SENT
    assert await _stored_claim(async_session_maker) == (M1, NOW + timedelta(minutes=15))


async def test_retry_refused_by_closed_window_leaves_claim_unchanged(
    async_session_maker, monkeypatch
):
    """Guards AC4 (claim is taken only after the window and settings checks pass)."""
    await _seed(
        async_session_maker,
        _contact(),
        _agent_message(),
        _inbound(NOW - timedelta(days=2)),
    )
    _configured(monkeypatch)
    post = _stub_http(monkeypatch, AsyncMock())
    assert (await _retry(async_session_maker, S1)).category == WINDOW_CLOSED
    assert await _stored_claim(async_session_maker) == (None, None)
    post.assert_not_awaited()


def _user(user_id):
    return User(
        id=user_id,
        name=user_id.upper(),
        email=f"{user_id}@example.com",
        password_hash="x",
        role="sales",
    )


_ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"assigned_to": S2}, (None, None)),
        ({"assigned_to": S1}, (S1, NOW + timedelta(minutes=10))),
        ({"company": "Acme"}, (S1, NOW + timedelta(minutes=10))),
    ],
    ids=["reassigned", "same-owner", "other-field"],
)
async def test_reassignment_clears_the_claim_only_when_the_owner_changes(
    async_session_maker, data, expected
):
    """Guards AC5 (a real owner change clears both claim columns in the same write; no
    owner change leaves them); design section 12 example 6."""
    await _seed(
        async_session_maker,
        _user(S1),
        _user(S2),
        _contact(reply_claimed_by=S1, reply_claimed_until=NOW + timedelta(minutes=10)),
    )
    async with async_session_maker() as session:
        await contact_service.update_contact(session, "c1", data, _ADMIN)
        await session.commit()
    assert await _stored_claim(async_session_maker) == expected
