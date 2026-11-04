"""CRM-TASK-017 (docs/chatbot-replies.md section 7): the CRM's own WhatsApp
entry points are gone, and a local env file that still carries the removed
keys does not stop the settings from loading (section 14, item 9)."""

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/webhooks/whatsapp"),
        ("POST", "/api/webhooks/whatsapp"),
        ("POST", "/internal/whatsapp/inbound"),
    ],
)
async def test_legacy_whatsapp_route_is_gone(client, method, path):
    resp = await client.request(method, path, json={})
    assert resp.status_code == 404


def test_env_file_with_removed_whatsapp_keys_still_loads(tmp_path):
    env_file = tmp_path / "local.env"
    env_file.write_text(
        "WHATSAPP_PHONE_NUMBER_ID=1\n"
        "WHATSAPP_ACCESS_TOKEN=x\n"
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN=x\n"
        "WHATSAPP_APP_SECRET=x\n"
        "INTERNAL_SHARED_SECRET=x\n"
        "WHATSAPP_GATEWAY_BASE_URL=http://gateway.invalid\n"
        "CHATBOT_SEND_SECRET=send\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_file)

    assert loaded.chatbot_send_secret == "send"
    assert not hasattr(loaded, "whatsapp_access_token")
