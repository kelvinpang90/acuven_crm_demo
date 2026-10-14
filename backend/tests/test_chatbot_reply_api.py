"""CRM-TASK-015 API mappings for chatbot replies, retries and handback."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_event_service, chatbot_send_service

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")


async def _seed(maker, *, deleted=False):
    async with maker() as session:
        session.add_all(
            [
                User(
                    id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales"
                ),
                Contact(
                    id="contact",
                    name="Contact",
                    phone="60123456789",
                    assigned_to="rep",
                    deleted_at=datetime.utcnow() if deleted else None,
                ),
            ]
        )
        await session.commit()


async def _request(client, user, method, path, **kwargs):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await getattr(client, method)(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    "category,status,code",
    [
        (chatbot_send_service.NO_PHONE, 400, "NO_PHONE"),
        (chatbot_send_service.WINDOW_CLOSED, 409, "WINDOW_CLOSED"),
        (chatbot_send_service.NOT_CONFIGURED, 503, "NOT_CONFIGURED"),
        (chatbot_send_service.SEND_FAILED, 502, "SEND_FAILED"),
    ],
)
async def test_send_maps_chatbot_failures(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards send acceptance criterion: each chatbot outcome has its API mapping."""
    await _seed(async_session_maker)
    result = chatbot_send_service.SendReplyResult(
        category,
        window_until=datetime(2026, 1, 1),
        error_code="META",
        error_title="Rejected",
    )
    monkeypatch.setattr(chatbot_send_service, "send_agent_reply", AsyncMock(return_value=result))
    response = await _request(
        client,
        REP,
        "post",
        "/api/messages/whatsapp/send",
        json={"contact_id": "contact", "message": " hi "},
    )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_send_validates_body_before_handler(client, async_session_maker):
    """Guards send acceptance criterion: blank text is rejected by the request model."""
    await _seed(async_session_maker)
    response = await _request(
        client,
        REP,
        "post",
        "/api/messages/whatsapp/send",
        json={"contact_id": "contact", "message": "  "},
    )
    assert response.status_code == 422


async def test_retry_hides_missing_message(client, async_session_maker):
    """Guards retry acceptance criterion: absent messages read as NOT_ASSIGNED."""
    await _seed(async_session_maker)
    response = await _request(client, REP, "post", "/api/messages/missing/whatsapp/retry")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"


async def test_handback_hides_deleted_contact(client, async_session_maker):
    """Guards handback acceptance criterion: deleted contacts read as NOT_ASSIGNED to admins too."""
    await _seed(async_session_maker, deleted=True)
    response = await _request(client, ADMIN, "post", "/api/contacts/contact/whatsapp/handback")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"


async def test_agent_reply_requires_crm_message_id(client, monkeypatch):
    """Guards agent_reply acceptance criterion: crm_message_id is mandatory."""
    monkeypatch.setattr("app.routers.internal_chatbot._configured_secrets", lambda: ["secret"])
    monkeypatch.setattr("app.routers.internal_chatbot.signature_ok", lambda *args: True)
    response = await client.post(
        "/internal/chatbot/events",
        json={
            "type": "agent_reply",
            "occurred_at": "2026-01-01T00:00:00Z",
            "visitor": {"phone": "60123456789"},
            "message": {"wa_message_id": "wa", "body": "reply"},
        },
    )
    assert response.status_code == 422


async def test_agent_reply_delegates_to_rule(client, monkeypatch):
    """Guards agent_reply acceptance criterion: valid callbacks use CRM-TASK-014 storage rules."""
    monkeypatch.setattr("app.routers.internal_chatbot._configured_secrets", lambda: ["secret"])
    monkeypatch.setattr("app.routers.internal_chatbot.signature_ok", lambda *args: True)
    mocked = AsyncMock(return_value="stored")
    monkeypatch.setattr(chatbot_event_service, "store_agent_reply", mocked)
    response = await client.post(
        "/internal/chatbot/events",
        json={
            "type": "agent_reply",
            "occurred_at": "2026-01-01T00:00:00Z",
            "visitor": {"phone": "60123456789"},
            "message": {"wa_message_id": "wa", "body": "reply"},
            "crm_message_id": "crm",
        },
    )
    assert response.json() == {"result": "stored"}
    mocked.assert_awaited_once()


async def test_message_sender_and_contact_reply_window_fields(client, async_session_maker):
    """Guards field acceptance criteria: messages expose sender data and contacts expose a UTC reply window."""
    await _seed(async_session_maker)
    inbound_at = datetime(2026, 1, 1, 12, 0, 0)
    async with async_session_maker() as session:
        session.add_all(
            [
                Message(
                    id="inbound",
                    contact_id="contact",
                    channel="whatsapp",
                    direction="inbound",
                    sender_id="visitor",
                    recipient_id="crm",
                    body="hello",
                    is_read=False,
                    assigned_to="rep",
                    created_at=inbound_at,
                ),
                Message(
                    id="agent",
                    contact_id="contact",
                    channel="whatsapp",
                    direction="outbound",
                    sender_id="crm",
                    recipient_id="visitor",
                    body="reply",
                    is_read=True,
                    assigned_to="rep",
                    sent_by="rep",
                    author_kind="agent",
                ),
            ]
        )
        await session.commit()
    messages = await _request(client, ADMIN, "get", "/api/messages/contact/contact")
    agent = next(item for item in messages.json()["data"] if item["id"] == "agent")
    assert agent["sent_by"] == "rep"
    assert agent["sent_by_name"] == "Rep"
    contact = await _request(client, ADMIN, "get", "/api/contacts/contact")
    assert contact.json()["data"]["wa_reply_window_until"] == "2026-01-02T12:00:00Z"
