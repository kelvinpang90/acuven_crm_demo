"""CRM-TASK-015 API mappings for chatbot replies, retries and handback."""

from datetime import datetime
from unittest.mock import ANY, AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_event_service, chatbot_send_service

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="other", name="Other", email="other@example.com", password_hash="x", role="sales")


async def _seed(maker, *, deleted=False, phone="60123456789"):
    async with maker() as session:
        session.add_all(
            [
                User(
                    id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales"
                ),
                User(
                    id="other",
                    name="Other",
                    email="other@example.com",
                    password_hash="x",
                    role="sales",
                ),
                Contact(
                    id="contact",
                    name="Contact",
                    phone=phone,
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


def _agent_message(message_id="agent", *, sent_by="rep"):
    return Message(
        id=message_id,
        contact_id="contact",
        channel="whatsapp",
        direction="outbound",
        sender_id="crm",
        recipient_id="visitor",
        body="reply",
        is_read=True,
        assigned_to="rep",
        sent_by=sent_by,
        author_kind="agent",
    )


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
        category, window_until=datetime(2026, 1, 1), error_code="META", error_title="Rejected"
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


async def test_send_success_and_unconfirmed_include_sender(
    client, async_session_maker, monkeypatch
):
    """Guards send acceptance criterion: sent and unconfirmed replies expose their sender."""
    await _seed(async_session_maker)
    for category, expected_status, expected_message in (
        (chatbot_send_service.SENT, 200, "OK"),
        (chatbot_send_service.UNCONFIRMED, 202, "UNCONFIRMED"),
    ):
        monkeypatch.setattr(
            chatbot_send_service,
            "send_agent_reply",
            AsyncMock(
                return_value=chatbot_send_service.SendReplyResult(
                    category, message=_agent_message()
                )
            ),
        )
        response = await _request(
            client,
            REP,
            "post",
            "/api/messages/whatsapp/send",
            json={"contact_id": "contact", "message": "hi"},
        )
        assert response.status_code == expected_status
        assert response.json()["success"] is True
        assert response.json()["message"] == expected_message
        assert response.json()["data"]["sent_by"] == "rep"
        assert response.json()["data"]["sent_by_name"] == "Rep"


async def test_send_failure_fields_are_utc_and_preserved(client, async_session_maker, monkeypatch):
    """Guards send acceptance criterion: window and Meta failure fields keep their values."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        chatbot_send_service,
        "send_agent_reply",
        AsyncMock(
            return_value=chatbot_send_service.SendReplyResult(
                chatbot_send_service.WINDOW_CLOSED, window_until=datetime(2026, 1, 1)
            )
        ),
    )
    response = await _request(
        client,
        REP,
        "post",
        "/api/messages/whatsapp/send",
        json={"contact_id": "contact", "message": "hi"},
    )
    assert response.json()["error"]["fields"] == {"window_closed_at": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr(
        chatbot_send_service,
        "send_agent_reply",
        AsyncMock(
            return_value=chatbot_send_service.SendReplyResult(
                chatbot_send_service.SEND_FAILED, error_code="META", error_title="Rejected"
            )
        ),
    )
    response = await _request(
        client,
        REP,
        "post",
        "/api/messages/whatsapp/send",
        json={"contact_id": "contact", "message": "hi"},
    )
    assert response.json()["error"]["fields"] == {"error_code": "META", "error_title": "Rejected"}


@pytest.mark.parametrize(
    "phone,with_inbound,expected_code",
    [
        (None, False, "NO_PHONE"),
        ("60123456789", False, "WINDOW_CLOSED"),
        ("60123456789", True, "NOT_CONFIGURED"),
    ],
)
async def test_send_checks_phone_then_window_then_configuration(
    client, async_session_maker, monkeypatch, phone, with_inbound, expected_code
):
    """Guards send acceptance criterion: NO_PHONE, WINDOW_CLOSED and NOT_CONFIGURED are ordered."""
    await _seed(async_session_maker, phone=phone)
    if with_inbound:
        async with async_session_maker() as session:
            session.add(
                Message(
                    id="inbound",
                    contact_id="contact",
                    channel="whatsapp",
                    direction="inbound",
                    sender_id="visitor",
                    recipient_id="crm",
                    body="hello",
                    assigned_to="rep",
                    created_at=datetime.utcnow(),
                )
            )
            await session.commit()
    monkeypatch.setattr(chatbot_send_service.settings, "chatbot_send_url", None)
    monkeypatch.setattr(chatbot_send_service.settings, "chatbot_send_secret", None)
    response = await _request(
        client,
        REP,
        "post",
        "/api/messages/whatsapp/send",
        json={"contact_id": "contact", "message": "hi"},
    )
    assert response.json()["error"]["code"] == expected_code


@pytest.mark.parametrize("deleted,user", [(False, REP), (True, ADMIN)])
async def test_send_hides_missing_or_deleted_contacts(client, async_session_maker, deleted, user):
    """Guards send acceptance criterion: missing and deleted contacts are NOT_ASSIGNED, including admins."""
    await _seed(async_session_maker, deleted=deleted)
    contact_id = "contact" if deleted else "missing"
    response = await _request(
        client,
        user,
        "post",
        "/api/messages/whatsapp/send",
        json={"contact_id": contact_id, "message": "hi"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"


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


async def test_retry_hides_nonexistent_non_agent_and_inaccessible_messages(
    client, async_session_maker
):
    """Guards retry acceptance criterion: all inaccessible messages share the identical hidden response."""
    await _seed(async_session_maker)
    async with async_session_maker() as session:
        session.add(
            Message(
                id="non-agent",
                contact_id="contact",
                channel="whatsapp",
                direction="outbound",
                sender_id="crm",
                recipient_id="visitor",
                body="reply",
                assigned_to="rep",
            )
        )
        session.add(_agent_message("agent"))
        await session.commit()
    missing = await _request(client, OTHER, "post", "/api/messages/missing/whatsapp/retry")
    non_agent = await _request(client, REP, "post", "/api/messages/non-agent/whatsapp/retry")
    inaccessible = await _request(client, OTHER, "post", "/api/messages/agent/whatsapp/retry")
    assert missing.json() == non_agent.json() == inaccessible.json()
    assert missing.status_code == 403


async def test_retry_hides_deleted_contact_from_admin(client, async_session_maker):
    """Guards retry acceptance criterion: a deleted contact is NOT_ASSIGNED even for admins."""
    await _seed(async_session_maker, deleted=True)
    async with async_session_maker() as session:
        session.add(_agent_message())
        await session.commit()
    response = await _request(client, ADMIN, "post", "/api/messages/agent/whatsapp/retry")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"


@pytest.mark.parametrize(
    "category,status,code",
    [
        (chatbot_send_service.NO_PHONE, 400, "NO_PHONE"),
        (chatbot_send_service.WINDOW_CLOSED, 409, "WINDOW_CLOSED"),
        (chatbot_send_service.NOT_CONFIGURED, 503, "NOT_CONFIGURED"),
        (chatbot_send_service.SEND_FAILED, 502, "SEND_FAILED"),
        (chatbot_send_service.IN_PROGRESS, 409, "IN_PROGRESS"),
        (chatbot_send_service.RETRY_EXPIRED, 409, "RETRY_EXPIRED"),
    ],
)
async def test_retry_maps_all_failure_results(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards retry acceptance criterion: shared and retry-only result mappings are preserved."""
    await _seed(async_session_maker)
    async with async_session_maker() as session:
        session.add(_agent_message())
        await session.commit()
    monkeypatch.setattr(
        chatbot_send_service,
        "retry_agent_reply",
        AsyncMock(
            return_value=chatbot_send_service.SendReplyResult(category, message=_agent_message())
        ),
    )
    response = await _request(client, REP, "post", "/api/messages/agent/whatsapp/retry")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_retry_confirmed_and_unconfirmed_include_sender(
    client, async_session_maker, monkeypatch
):
    """Guards retry acceptance criterion: a confirmed retry and an unconfirmed reply return sender fields."""
    await _seed(async_session_maker)
    async with async_session_maker() as session:
        confirmed = _agent_message()
        confirmed.external_id = "wa-confirmed"
        session.add(confirmed)
        await session.commit()
    response = await _request(client, REP, "post", "/api/messages/agent/whatsapp/retry")
    assert response.status_code == 200
    assert response.json()["data"]["sent_by"] == "rep"
    assert response.json()["data"]["sent_by_name"] == "Rep"
    monkeypatch.setattr(
        chatbot_send_service,
        "retry_agent_reply",
        AsyncMock(
            return_value=chatbot_send_service.SendReplyResult(
                chatbot_send_service.UNCONFIRMED, message=_agent_message()
            )
        ),
    )
    response = await _request(client, REP, "post", "/api/messages/agent/whatsapp/retry")
    assert response.status_code == 202
    assert response.json()["message"] == "UNCONFIRMED"
    assert response.json()["data"]["sent_by"] == "rep"
    assert response.json()["data"]["sent_by_name"] == "Rep"


async def test_handback_hides_missing_deleted_and_unassigned_contacts(client, async_session_maker):
    """Guards handback acceptance criterion: every inaccessible contact is NOT_ASSIGNED."""
    await _seed(async_session_maker)
    missing = await _request(client, REP, "post", "/api/contacts/missing/whatsapp/handback")
    unassigned = await _request(client, OTHER, "post", "/api/contacts/contact/whatsapp/handback")
    assert missing.json() == unassigned.json()
    async with async_session_maker() as session:
        contact = await session.get(Contact, "contact")
        contact.deleted_at = datetime.utcnow()
        await session.commit()
    deleted = await _request(client, ADMIN, "post", "/api/contacts/contact/whatsapp/handback")
    assert deleted.json() == missing.json()


@pytest.mark.parametrize(
    "category,status,code",
    [
        (chatbot_send_service.NO_IDENTITY, 400, "NO_IDENTITY"),
        (chatbot_send_service.NOT_CONFIGURED, 503, "NOT_CONFIGURED"),
        (chatbot_send_service.CHATBOT_FAILED, 502, "CHATBOT_FAILED"),
    ],
)
async def test_handback_maps_chatbot_failures(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards handback acceptance criterion: every service failure has its specified API mapping."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        chatbot_send_service,
        "handback_to_ai",
        AsyncMock(return_value=chatbot_send_service.HandbackResult(category)),
    )
    response = await _request(client, REP, "post", "/api/contacts/contact/whatsapp/handback")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_handback_returns_takeover_fields(client, async_session_maker, monkeypatch):
    """Guards handback acceptance criterion: successful output follows the three-field takeover contract."""
    await _seed(async_session_maker)
    contact = Contact(
        id="returned",
        name="Returned",
        wa_handover_active=False,
        wa_handover_expires_at=None,
        wa_handover_updated_at=datetime(2026, 1, 1),
    )
    monkeypatch.setattr(
        chatbot_send_service,
        "handback_to_ai",
        AsyncMock(
            return_value=chatbot_send_service.HandbackResult(
                chatbot_send_service.ENDED, contact=contact
            )
        ),
    )
    response = await _request(client, REP, "post", "/api/contacts/contact/whatsapp/handback")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "wa_handover_active": False,
        "wa_handover_expires_at": None,
        "wa_handover_updated_at": "2026-01-01T00:00:00Z",
    }


def _event_payload(event_type, **extra):
    payload = {
        "type": event_type,
        "occurred_at": "2026-01-01T00:00:00Z",
        "visitor": {"phone": "60123456789"},
    }
    payload.update(extra)
    return payload


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"message": {"body": "reply"}},
        {"message": {"wa_message_id": "wa"}},
        {"message": {"wa_message_id": "wa", "body": "reply"}, "crm_message_id": ""},
    ],
)
async def test_agent_reply_requires_all_reply_fields(client, monkeypatch, extra):
    """Guards agent_reply acceptance criterion: message, both message fields and non-empty CRM id are required."""
    monkeypatch.setattr("app.routers.internal_chatbot._configured_secrets", lambda: ["secret"])
    monkeypatch.setattr("app.routers.internal_chatbot.signature_ok", lambda *args: True)
    response = await client.post(
        "/internal/chatbot/events", json=_event_payload("agent_reply", **extra)
    )
    assert response.status_code == 422


async def test_agent_reply_delegates_to_rule(client, monkeypatch):
    """Guards agent_reply acceptance criterion: valid callbacks pass their exact reply values to CRM-TASK-014."""
    monkeypatch.setattr("app.routers.internal_chatbot._configured_secrets", lambda: ["secret"])
    monkeypatch.setattr("app.routers.internal_chatbot.signature_ok", lambda *args: True)
    mocked = AsyncMock(return_value="stored")
    monkeypatch.setattr(chatbot_event_service, "store_agent_reply", mocked)
    response = await client.post(
        "/internal/chatbot/events",
        json=_event_payload(
            "agent_reply", message={"wa_message_id": "wa", "body": "reply"}, crm_message_id="crm"
        ),
    )
    assert response.json() == {"result": "stored"}
    mocked.assert_awaited_once_with(ANY, crm_message_id="crm", wa_message_id="wa", body="reply")


@pytest.mark.parametrize(
    "event_type,extra",
    [
        ("customer_message", {"message": {"wa_message_id": "wa", "body": "body"}}),
        ("ai_reply", {"message": {"wa_message_id": "wa", "body": "body"}, "bot_id": "bot"}),
        ("console_reply", {"message": {"wa_message_id": "wa", "body": "body"}}),
        ("handover_changed", {"handover": {"active": False, "expires_at": None}}),
        ("delivery_status", {"status": {"wa_message_id": "wa", "status": "sent"}}),
    ],
)
async def test_existing_event_types_still_delegate_unchanged(
    client, monkeypatch, event_type, extra
):
    """Guards agent_reply acceptance criterion: the five existing event kinds keep their original delegation path."""
    monkeypatch.setattr("app.routers.internal_chatbot._configured_secrets", lambda: ["secret"])
    monkeypatch.setattr("app.routers.internal_chatbot.signature_ok", lambda *args: True)
    mocked = AsyncMock(return_value="stored")
    monkeypatch.setattr(chatbot_event_service, "process_event", mocked)
    response = await client.post(
        "/internal/chatbot/events", json=_event_payload(event_type, **extra)
    )
    assert response.json() == {"result": "stored"}
    mocked.assert_awaited_once()


async def test_message_endpoints_include_sender_and_missing_name(client, async_session_maker):
    """Guards message field acceptance criterion: list, contact list and mark-read expose sender fields, absent users as null."""
    await _seed(async_session_maker)
    async with async_session_maker() as session:
        session.add_all(
            [_agent_message("agent", sent_by="rep"), _agent_message("gone", sent_by="gone")]
        )
        await session.commit()
    listing = await _request(client, ADMIN, "get", "/api/messages")
    listed = {item["id"]: item for item in listing.json()["data"]["data"]}
    assert listed["agent"]["sent_by"] == "rep"
    assert listed["agent"]["sent_by_name"] == "Rep"
    assert listed["gone"]["sent_by_name"] is None
    contact_messages = await _request(client, ADMIN, "get", "/api/messages/contact/contact")
    contact_agent = next(item for item in contact_messages.json()["data"] if item["id"] == "agent")
    assert contact_agent["sent_by"] == "rep"
    assert contact_agent["sent_by_name"] == "Rep"
    marked = await _request(client, ADMIN, "patch", "/api/messages/agent/read")
    assert marked.json()["data"]["sent_by"] == "rep"
    assert marked.json()["data"]["sent_by_name"] == "Rep"


async def test_contact_list_and_detail_reply_windows(client, async_session_maker):
    """Guards contact field acceptance criterion: only inbound WhatsApp messages produce UTC reply windows, otherwise null."""
    await _seed(async_session_maker)
    async with async_session_maker() as session:
        session.add(Contact(id="quiet", name="Quiet", assigned_to="rep"))
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
                    assigned_to="rep",
                    created_at=datetime(2026, 1, 1, 12),
                ),
                Message(
                    id="email",
                    contact_id="quiet",
                    channel="email",
                    direction="inbound",
                    sender_id="visitor",
                    recipient_id="crm",
                    body="email",
                    assigned_to="rep",
                    created_at=datetime(2026, 1, 1, 12),
                ),
                Message(
                    id="outbound",
                    contact_id="quiet",
                    channel="whatsapp",
                    direction="outbound",
                    sender_id="crm",
                    recipient_id="visitor",
                    body="outbound",
                    assigned_to="rep",
                    created_at=datetime(2026, 1, 1, 12),
                ),
            ]
        )
        await session.commit()
    listing = await _request(client, ADMIN, "get", "/api/contacts")
    contacts = {item["id"]: item for item in listing.json()["data"]["data"]}
    assert contacts["contact"]["wa_reply_window_until"] == "2026-01-02T12:00:00Z"
    assert contacts["quiet"]["wa_reply_window_until"] is None
    detail = await _request(client, ADMIN, "get", "/api/contacts/contact")
    assert detail.json()["data"]["wa_reply_window_until"] == "2026-01-02T12:00:00Z"
