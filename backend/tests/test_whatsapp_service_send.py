from unittest.mock import AsyncMock, patch

from app.config import settings
from app.models.contact import Contact
from app.services import whatsapp_service


async def _make_contact(session_maker, *, is_gateway: bool) -> str:
    async with session_maker() as session:
        contact = Contact(id="c1", name="Test", phone="60199999999", is_gateway=is_gateway)
        session.add(contact)
        await session.commit()
        return contact.id


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
