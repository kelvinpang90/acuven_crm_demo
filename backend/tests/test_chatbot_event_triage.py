"""POST /internal/chatbot/events queues run_triage for a newly stored customer
message (docs/inbox-ai-triage.md section 2 and 4.2).

Each test names the acceptance criterion of CRM-TASK-067 it guards:
AC1 queue run_triage only for a stored customer_message with the switch on and
AI configured, AC2 the response is unchanged. run_triage and the signature check
are stubbed.
"""

import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.routers import internal_chatbot
from app.services import chatbot_event_service, message_triage_service

URL = "/internal/chatbot/events"
PHONE = "60123456789"
OTHER_PHONE = "60198765432"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Signature always accepted; switch on and AI configured unless a test says otherwise."""
    monkeypatch.setattr(settings, "chatbot_events_secret", "test-only-key")
    monkeypatch.setattr(settings, "chatbot_events_secret_previous", "")
    monkeypatch.setattr(internal_chatbot, "signature_ok", lambda *args: True)
    monkeypatch.setattr(settings, "ai_triage_enabled", True)
    monkeypatch.setattr(internal_chatbot.ai_api_client, "is_ai_configured", lambda: True)


@pytest.fixture
def triage_calls(monkeypatch) -> list:
    calls = []

    async def _fake_run_triage(contact_id, message_id, *args, **kwargs):
        calls.append((contact_id, message_id))

    monkeypatch.setattr(message_triage_service, "run_triage", _fake_run_triage)
    return calls


def _event(event_type: str, **changes) -> dict:
    body = {
        "event_id": "6f1c2b9e-1111-4222-8333-944455556666",
        "type": event_type,
        "occurred_at": "2026-10-12T08:00:00Z",
        "visitor": {"phone": PHONE, "bsuid": None, "profile_name": "Ann"},
    }
    if event_type == "customer_message":
        body["message"] = {"wa_message_id": "wamid.in", "body": "hello"}
    elif event_type == "agent_reply":
        body["crm_message_id"] = "m-agent"
        body["message"] = {"wa_message_id": "wamid.agent", "body": "A person here"}
    elif event_type == "handover_changed":
        body["handover"] = {"active": True, "expires_at": "2026-10-12T08:30:00Z"}
    elif event_type == "delivery_status":
        body["status"] = {"wa_message_id": "wamid.out", "status": "delivered"}
    body.update(changes)
    return body


async def _post(client, body: dict):
    raw = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Acuven-Timestamp": "1760256000",
        "X-Acuven-Signature": "sha256=" + "0" * 64,
    }
    return await client.post(URL, content=raw, headers=headers)


async def _seed(maker) -> None:
    """Contact c1 with PHONE, one outbound chatbot message and one unconfirmed agent message."""
    async with maker() as session:
        session.add_all(
            [
                Contact(id="c1", name="Ann", phone=PHONE),
                Message(
                    id="m-out",
                    contact_id="c1",
                    channel="whatsapp",
                    direction="outbound",
                    sender_id="ai_chatbot_demo",
                    recipient_id=PHONE,
                    body="earlier reply",
                    external_id="wamid.out",
                    is_read=True,
                ),
                Message(
                    id="m-agent",
                    contact_id="c1",
                    channel="whatsapp",
                    direction="outbound",
                    sender_id="agent",
                    recipient_id=PHONE,
                    body="A person here",
                    author_kind="agent",
                    is_read=True,
                ),
            ]
        )
        await session.commit()


async def _message_by_external_id(maker, external_id: str) -> Message:
    async with maker() as session:
        query = select(Message).where(Message.external_id == external_id)
        return (await session.execute(query)).scalar_one()


# --- AC1: a newly stored customer_message queues run_triage once ---


async def test_stored_customer_message_queues_run_triage(client, async_session_maker, triage_calls):
    """AC1 (test 1): switch on, AI configured, new customer_message stored -> run_triage
    queued once with that message's contact_id and id."""
    await _seed(async_session_maker)
    resp = await _post(client, _event("customer_message"))
    assert resp.json() == {"result": "stored"}

    message = await _message_by_external_id(async_session_maker, "wamid.in")
    assert message.contact_id == "c1"
    assert triage_calls == [("c1", message.id)]


# --- AC1: other events and results do not queue ---


async def test_duplicate_customer_message_does_not_queue(client, async_session_maker, triage_calls):
    """AC1 (test 2): the same customer_message again -> duplicate, nothing more queued."""
    await _seed(async_session_maker)
    await _post(client, _event("customer_message"))
    assert len(triage_calls) == 1

    again = await _post(client, _event("customer_message"))
    assert again.json() == {"result": "duplicate"}
    assert len(triage_calls) == 1


async def test_ignored_customer_message_does_not_queue(
    client, async_session_maker, triage_calls, monkeypatch
):
    """AC1 (test 2): customer_message whose result is ignored -> not queued, even though
    a message with that external_id exists."""
    await _seed(async_session_maker)
    async with async_session_maker() as session:
        session.add(
            Message(
                id="m-in",
                contact_id="c1",
                channel="whatsapp",
                direction="inbound",
                sender_id=PHONE,
                recipient_id="ai_chatbot_demo",
                body="hello",
                external_id="wamid.in",
                author_kind="customer",
            )
        )
        await session.commit()

    async def _ignored(*args, **kwargs):
        return chatbot_event_service.IGNORED

    monkeypatch.setattr(chatbot_event_service, "process_event", _ignored)
    resp = await _post(client, _event("customer_message"))
    assert resp.json() == {"result": "ignored"}
    assert triage_calls == []


@pytest.mark.parametrize(
    "event_type", ["agent_reply", "handover_changed", "delivery_status", "ai_reply"]
)
async def test_other_event_types_do_not_queue(
    client, async_session_maker, triage_calls, event_type
):
    """AC1 (test 2): agent_reply, handover and delivery events (and ai_reply) are stored
    but never queue run_triage."""
    await _seed(async_session_maker)
    body = _event(event_type)
    if event_type == "ai_reply":
        body["message"] = {"wa_message_id": "wamid.ai", "body": "Hi"}
    resp = await _post(client, body)
    assert resp.json() == {"result": "stored"}
    assert triage_calls == []


async def test_ignored_handover_does_not_queue(client, async_session_maker, triage_calls):
    """AC1 (test 2): a handover event for an unknown visitor -> ignored, not queued."""
    await _seed(async_session_maker)
    visitor = {"phone": OTHER_PHONE, "bsuid": None}
    resp = await _post(client, _event("handover_changed", visitor=visitor))
    assert resp.json() == {"result": "ignored"}
    assert triage_calls == []


# --- AC1: switch off or AI not configured ---


async def test_switch_off_does_not_queue(client, async_session_maker, triage_calls, monkeypatch):
    """AC1 (test 3): AI_TRIAGE_ENABLED false -> a stored customer_message is not queued."""
    monkeypatch.setattr(settings, "ai_triage_enabled", False)
    await _seed(async_session_maker)
    resp = await _post(client, _event("customer_message"))
    assert resp.json() == {"result": "stored"}
    assert triage_calls == []


async def test_ai_not_configured_does_not_queue(
    client, async_session_maker, triage_calls, monkeypatch
):
    """AC1 (test 3): switch on but AI not configured -> not queued."""
    monkeypatch.setattr(internal_chatbot.ai_api_client, "is_ai_configured", lambda: False)
    await _seed(async_session_maker)
    resp = await _post(client, _event("customer_message"))
    assert resp.json() == {"result": "stored"}
    assert triage_calls == []


# --- AC2: the response does not change ---


async def test_response_same_as_with_switch_off(
    client, async_session_maker, triage_calls, monkeypatch
):
    """AC2 (test 4): status, body and headers are the same with the switch on and off."""
    await _seed(async_session_maker)
    on = await _post(client, _event("customer_message"))
    assert len(triage_calls) == 1

    monkeypatch.setattr(settings, "ai_triage_enabled", False)
    message = {"wa_message_id": "wamid.in-2", "body": "hello"}
    off = await _post(client, _event("customer_message", message=message))
    assert len(triage_calls) == 1

    assert on.status_code == off.status_code == 200
    assert on.content == off.content
    assert on.json() == {"result": "stored"}
    assert dict(on.headers) == dict(off.headers)
