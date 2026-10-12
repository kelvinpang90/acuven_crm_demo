from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import whatsapp_service

ADMIN = User(id="u-admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")


async def _make_contact(session_maker, *, is_gateway: bool) -> str:
    async with session_maker() as session:
        contact = Contact(id="c1", name="Test", phone="60199999999", is_gateway=is_gateway)
        session.add(contact)
        await session.commit()
        return contact.id


async def _outbound_count(session_maker) -> int:
    async with session_maker() as session:
        return (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.direction == "outbound")
            )
        ).scalar()


async def _post_send(client, contact_id: str):
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    try:
        return await client.post(
            "/api/messages/whatsapp/send",
            json={"contact_id": contact_id, "message": "hi there"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_send_message_demo_contact_uses_gateway(async_session_maker, monkeypatch):
    """Guards AC2: the demo-contact gateway branch is unchanged."""
    monkeypatch.setattr(settings, "whatsapp_gateway_base_url", "http://gateway.internal")
    monkeypatch.setattr(settings, "internal_shared_secret", "shared-secret")

    contact_id = await _make_contact(async_session_maker, is_gateway=True)

    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
        async with async_session_maker() as session:
            result = await whatsapp_service.send_message(session, contact_id, "hello")

    assert result["body"] == "hello"
    mock_post.assert_awaited_once()
    called_url = mock_post.call_args.args[0]
    called_kwargs = mock_post.call_args.kwargs
    assert called_url == "http://gateway.internal/internal/whatsapp/outbound"
    assert called_kwargs["headers"]["X-Internal-Secret"] == "shared-secret"
    assert called_kwargs["json"] == {"to": "60199999999", "text": "hello"}


@pytest.mark.parametrize(
    "token, phone_number_id",
    [("", ""), ("", "123456"), ("token", "")],
)
async def test_send_without_credentials_is_503_and_not_saved(
    client, async_session_maker, monkeypatch, token, phone_number_id
):
    """Guards AC1: a missing access token or phone number id answers 503
    NOT_CONFIGURED, persists no outbound message and makes no outside call."""
    monkeypatch.setattr(settings, "whatsapp_access_token", token)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", phone_number_id)

    contact_id = await _make_contact(async_session_maker, is_gateway=False)

    with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
        resp = await _post_send(client, contact_id)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NOT_CONFIGURED"
    mock_post.assert_not_awaited()
    assert await _outbound_count(async_session_maker) == 0


async def test_send_with_credentials_succeeds_and_is_saved(
    client, async_session_maker, monkeypatch
):
    """Guards AC2: with credentials set, a 200 from the Graph API still sends
    and persists the message with its external id."""
    monkeypatch.setattr(settings, "whatsapp_access_token", "token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123456")

    contact_id = await _make_contact(async_session_maker, is_gateway=False)

    graph_response = MagicMock(status_code=200)
    graph_response.json.return_value = {"messages": [{"id": "wamid.1"}]}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=graph_response)) as mock_post:
        resp = await _post_send(client, contact_id)

    assert resp.status_code == 200
    assert resp.json()["data"]["external_id"] == "wamid.1"
    mock_post.assert_awaited_once()
    assert mock_post.call_args.args[0] == f"{whatsapp_service.GRAPH_API}/123456/messages"
    assert await _outbound_count(async_session_maker) == 1


@pytest.mark.parametrize(
    "post_mock",
    [
        AsyncMock(return_value=MagicMock(status_code=400, text="bad request")),
        AsyncMock(side_effect=httpx.ConnectError("unreachable")),
    ],
    ids=["non_200", "network_error"],
)
async def test_send_graph_failure_is_502_and_not_saved(
    client, async_session_maker, monkeypatch, post_mock
):
    """Guards AC2: a non-200 from the Graph API or a network error is still
    502 API_ERROR with nothing persisted."""
    monkeypatch.setattr(settings, "whatsapp_access_token", "token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123456")

    contact_id = await _make_contact(async_session_maker, is_gateway=False)

    with patch("httpx.AsyncClient.post", new=post_mock):
        resp = await _post_send(client, contact_id)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "API_ERROR"
    assert await _outbound_count(async_session_maker) == 0
