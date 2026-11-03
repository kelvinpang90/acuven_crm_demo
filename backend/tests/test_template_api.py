"""CRM-TASK-098: template list and send-template endpoints, template retry through the
existing retry endpoint, and template_name on returned messages.

AC1 = GET /api/messages/whatsapp/templates, AC2 = send-template request model (422),
AC3 = send-template ownership and result mapping (docs/chatbot-phase3.md section 15 item 8),
AC4 = template retry through the existing endpoint and template_name on messages.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_send_service, chatbot_template_service
from app.services.chatbot_send_service import SendReplyResult
from app.services.chatbot_template_service import TemplateListResult

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="other", name="Other", email="other@example.com", password_hash="x", role="sales")

PHONE = "60123456789"
TEMPLATES = "/api/messages/whatsapp/templates"
SEND = "/api/messages/whatsapp/send-template"
FOLLOWUP = {
    "name": "crm_followup",
    "language": "en",
    "category": "MARKETING",
    "params": [{"label": "Name", "max_len": 10}, {"label": "Company", "max_len": 20}],
    "body": "Hi {{1}}, {{2}} would like to follow up.",
}
HELLO = {"name": "crm_hello", "language": "en", "params": [], "body": "Hello"}


def _body(**overrides):
    body = {
        "contact_id": "contact",
        "template_name": "crm_followup",
        "language": "en",
        "params": ["Mei", "Acuven"],
    }
    body.update(overrides)
    return body


async def _seed(maker, *rows, deleted=False, phone=PHONE):
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
                    name="Mei",
                    phone=phone,
                    assigned_to="rep",
                    deleted_at=datetime.utcnow() if deleted else None,
                ),
                *rows,
            ]
        )
        await session.commit()


async def _request(client, user, method, path, **kwargs):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await getattr(client, method)(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _template_message(message_id="tmpl", **kwargs):
    values = {
        "id": message_id,
        "contact_id": "contact",
        "channel": "whatsapp",
        "direction": "outbound",
        "sender_id": "ai_chatbot_demo",
        "recipient_id": PHONE,
        "body": "Hi Mei, Acuven would like to follow up.",
        "is_read": True,
        "assigned_to": "rep",
        "author_kind": "agent",
        "sent_by": "rep",
        "template_name": "crm_followup",
        "template_language": "en",
        "template_params": ["Mei", "Acuven"],
    }
    values.update(kwargs)
    return Message(**values)


def _text_message(message_id="text", **kwargs):
    values = {
        "id": message_id,
        "contact_id": "contact",
        "channel": "whatsapp",
        "direction": "outbound",
        "sender_id": "ai_chatbot_demo",
        "recipient_id": PHONE,
        "body": "plain reply",
        "is_read": True,
        "assigned_to": "rep",
        "author_kind": "agent",
        "sent_by": "rep",
    }
    values.update(kwargs)
    return Message(**values)


def _inbound(created_at):
    return Message(
        id="inbound",
        contact_id="contact",
        channel="whatsapp",
        direction="inbound",
        sender_id=PHONE,
        recipient_id="ai_chatbot_demo",
        body="hello",
        is_read=True,
        assigned_to="rep",
        created_at=created_at,
    )


def _mock_list(monkeypatch, result):
    mocked = AsyncMock(return_value=result)
    monkeypatch.setattr(chatbot_template_service, "list_templates", mocked)
    return mocked


def _mock_send(monkeypatch, result):
    mocked = AsyncMock(return_value=result)
    monkeypatch.setattr(chatbot_template_service, "send_template", mocked)
    return mocked


def _chatbot(monkeypatch, send_outcome):
    """Configure chatbot and stub its signed calls; ``send_outcome`` is the
    (status, payload) of every send call. Returns the recorded (path, payload)."""
    monkeypatch.setattr(settings, "chatbot_send_url", "http://chatbot.internal:8000")
    monkeypatch.setattr(settings, "chatbot_send_secret", "secret")
    calls = []

    async def post(path, payload):
        calls.append((path, payload))
        if path == "/internal/crm/templates":
            return SimpleNamespace(status_code=200), {"templates": [FOLLOWUP, HELLO]}, False
        status, response_payload = send_outcome
        return SimpleNamespace(status_code=status), response_payload, False

    monkeypatch.setattr(chatbot_send_service, "_post_chatbot", post)
    monkeypatch.setattr(chatbot_template_service, "_post_chatbot", post)
    return calls


# --- AC1: template list -------------------------------------------------------


async def test_template_list_returns_the_whitelist_fields(client, async_session_maker, monkeypatch):
    """Guards AC1 (200 with data as the list: name, language, category only when present,
    params as label and max_len, body; nothing else of chatbot's item is passed on)."""
    extra = {**FOLLOWUP, "secret_note": "x", "params": [{"label": "Name", "max_len": 10, "x": 1}]}
    extra["body"] = "Hi {{1}}"
    _mock_list(monkeypatch, TemplateListResult(chatbot_template_service.OK, [extra, HELLO]))

    response = await _request(client, REP, "get", TEMPLATES)

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"] == [
        {
            "name": "crm_followup",
            "language": "en",
            "category": "MARKETING",
            "params": [{"label": "Name", "max_len": 10}],
            "body": "Hi {{1}}",
        },
        {"name": "crm_hello", "language": "en", "params": [], "body": "Hello"},
    ]


async def test_template_list_of_an_empty_whitelist_is_empty(
    client, async_session_maker, monkeypatch
):
    """Guards AC1 (an empty whitelist is 200 with an empty list)."""
    _mock_list(monkeypatch, TemplateListResult(chatbot_template_service.OK, []))
    response = await _request(client, REP, "get", TEMPLATES)
    assert (response.status_code, response.json()["data"]) == (200, [])


@pytest.mark.parametrize(
    "category,status,code",
    [
        (chatbot_send_service.NOT_CONFIGURED, 503, "NOT_CONFIGURED"),
        (chatbot_send_service.CHATBOT_FAILED, 502, "CHATBOT_FAILED"),
    ],
)
async def test_template_list_failures(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards AC1 (NOT_CONFIGURED is 503, CHATBOT_FAILED is 502, in the fail envelope)."""
    _mock_list(monkeypatch, TemplateListResult(category))
    response = await _request(client, REP, "get", TEMPLATES)
    assert response.status_code == status
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("user", [REP, OTHER, ADMIN], ids=["owner", "other-sales", "admin"])
async def test_template_list_needs_only_a_login(client, async_session_maker, monkeypatch, user):
    """Guards AC1 (any logged-in user may read the list, whatever contacts they own)."""
    _mock_list(monkeypatch, TemplateListResult(chatbot_template_service.OK, [HELLO]))
    response = await _request(client, user, "get", TEMPLATES)
    assert response.status_code == 200


async def test_template_list_without_login_is_rejected(client, async_session_maker, monkeypatch):
    """Guards AC1 (login required: no token is refused before chatbot is read)."""
    mocked = _mock_list(monkeypatch, TemplateListResult(chatbot_template_service.OK, [HELLO]))
    response = await client.get(TEMPLATES)
    assert response.status_code in (401, 403)
    mocked.assert_not_awaited()


# --- AC2: request model -------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"contact_id": None},
        {"template_name": "   "},
        {"template_name": "t" * 101},
        {"language": ""},
        {"language": "l" * 21},
        {"params": None},
        {"params": ["p"] * 11},
        {"params": ["Mei", "line\nbreak"]},
        {"params": ["Mei", "carriage\rreturn"]},
        {"params": ["Mei", "tab\there"]},
        {"params": ["Mei", "five     spaces"]},
        {"params": ["Mei", "four    spaces"]},
        {"params": ["Mei", "   "]},
        {"params": ["Mei", "x" * 1025]},
        {"params": ["Mei", 7]},
        {"params": "Mei"},
    ],
    ids=[
        "no-contact",
        "blank-name",
        "name-101",
        "empty-language",
        "language-21",
        "no-params",
        "11-params",
        "newline",
        "carriage-return",
        "tab",
        "5-spaces",
        "4-spaces",
        "blank-param",
        "param-1025",
        "param-not-string",
        "params-not-list",
    ],
)
async def test_send_template_request_model_rejects_with_422(
    client, async_session_maker, monkeypatch, overrides
):
    """Guards AC2 (a non-conforming body is 422 from the request model, before the handler:
    neither ownership nor the service is reached)."""
    await _seed(async_session_maker)
    mocked = _mock_send(monkeypatch, SendReplyResult(chatbot_send_service.SENT))
    body = {key: value for key, value in _body(**overrides).items() if value is not None}
    # OTHER may not access the contact, so a 403 would show the handler ran.
    response = await _request(client, OTHER, "post", SEND, json=body)
    assert response.status_code == 422
    mocked.assert_not_awaited()


async def test_send_template_request_model_accepts_the_limits(
    client, async_session_maker, monkeypatch
):
    """Guards AC2 (100-character name after trimming, 20-character language, 10 params,
    1024-character params and three spaces in a row are accepted; the trimmed name is
    what the service gets)."""
    await _seed(async_session_maker)
    mocked = _mock_send(monkeypatch, SendReplyResult(chatbot_send_service.SENT, _text_message()))
    name = "n" * 100
    params = ["a   b", " " + "x" * 1024 + " "] + ["p"] * 8
    response = await _request(
        client,
        REP,
        "post",
        SEND,
        json=_body(template_name=f"  {name}  ", language="l" * 20, params=params),
    )
    assert response.status_code == 200
    kwargs = mocked.await_args.kwargs
    assert (kwargs["name"], kwargs["language"], kwargs["params"]) == (name, "l" * 20, params)
    assert (kwargs["contact_id"], kwargs["agent_id"], kwargs["agent_name"]) == (
        "contact",
        "rep",
        "Rep",
    )


async def test_send_template_with_no_params_is_accepted(client, async_session_maker, monkeypatch):
    """Guards AC2 (an empty params list is within 0 to 10)."""
    await _seed(async_session_maker)
    mocked = _mock_send(monkeypatch, SendReplyResult(chatbot_send_service.SENT, _text_message()))
    response = await _request(
        client, REP, "post", SEND, json=_body(template_name="crm_hello", params=[])
    )
    assert response.status_code == 200
    assert mocked.await_args.kwargs["params"] == []


# --- AC3: ownership -----------------------------------------------------------


@pytest.mark.parametrize(
    "user,contact_id,deleted",
    [
        (OTHER, "contact", False),
        (REP, "missing", False),
        (ADMIN, "missing", False),
        (ADMIN, "contact", True),
        (REP, "contact", True),
    ],
    ids=["not-assigned", "missing", "missing-admin", "deleted-admin", "deleted-owner"],
)
async def test_send_template_ownership_is_403_not_assigned(
    client, async_session_maker, monkeypatch, user, contact_id, deleted
):
    """Guards AC3 (may_access_contact as for text sends: no access, a missing or a deleted
    contact, admins included, is 403 NOT_ASSIGNED and the service is not called)."""
    await _seed(async_session_maker, deleted=deleted)
    mocked = _mock_send(monkeypatch, SendReplyResult(chatbot_send_service.SENT))
    response = await _request(client, user, "post", SEND, json=_body(contact_id=contact_id))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"
    mocked.assert_not_awaited()


async def test_send_template_admin_may_send_for_any_live_contact(
    client, async_session_maker, monkeypatch
):
    """Guards AC3 (ownership passes for an admin on another user's live contact)."""
    await _seed(async_session_maker)
    mocked = _mock_send(monkeypatch, SendReplyResult(chatbot_send_service.SENT, _text_message()))
    response = await _request(client, ADMIN, "post", SEND, json=_body())
    assert response.status_code == 200
    assert mocked.await_args.kwargs["agent_id"] == "admin"


# --- AC3: result mapping ------------------------------------------------------


@pytest.mark.parametrize(
    "category,status,code",
    [
        (chatbot_send_service.NO_PHONE, 400, "NO_PHONE"),
        (chatbot_template_service.NO_OPT_IN, 409, "NO_OPT_IN"),
        (chatbot_template_service.TEMPLATE_PENDING, 409, "TEMPLATE_PENDING"),
        (chatbot_send_service.NOT_CONFIGURED, 503, "NOT_CONFIGURED"),
        (chatbot_send_service.CHATBOT_FAILED, 502, "CHATBOT_FAILED"),
        (chatbot_template_service.TEMPLATE_UNKNOWN, 422, "TEMPLATE_UNKNOWN"),
        (chatbot_template_service.TEMPLATE_PARAMS, 422, "TEMPLATE_PARAMS"),
        (chatbot_send_service.NOT_FOUND, 403, "NOT_ASSIGNED"),
    ],
)
async def test_send_template_maps_each_error(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards AC3 (each service result has its status and code in the fail envelope)."""
    await _seed(async_session_maker)
    _mock_send(monkeypatch, SendReplyResult(category))
    response = await _request(client, REP, "post", SEND, json=_body())
    assert response.status_code == status
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == code


async def test_send_template_claimed_is_409_with_claim_fields(
    client, async_session_maker, monkeypatch
):
    """Guards AC3 (CLAIMED is 409 with the same fields as CRM-TASK-094)."""
    await _seed(async_session_maker)
    until = datetime(2026, 11, 1, 10, 10)
    _mock_send(
        monkeypatch,
        SendReplyResult(chatbot_send_service.CLAIMED, claimed_by="other", claimed_until=until),
    )
    response = await _request(client, REP, "post", SEND, json=_body())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLAIMED"
    assert response.json()["error"]["fields"] == {
        "claimed_by": "other",
        "claimed_by_name": "Other",
        "claimed_until": "2026-11-01T10:10:00Z",
    }


async def test_send_template_send_failed_is_502_with_meta_fields(
    client, async_session_maker, monkeypatch
):
    """Guards AC3 (explicitly not sent is 502 SEND_FAILED, fields carry error_code and
    error_title)."""
    await _seed(async_session_maker)
    _mock_send(
        monkeypatch,
        SendReplyResult(
            chatbot_send_service.SEND_FAILED, error_code=132000, error_title="Param mismatch"
        ),
    )
    response = await _request(client, REP, "post", SEND, json=_body())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SEND_FAILED"
    assert response.json()["error"]["fields"] == {
        "error_code": 132000,
        "error_title": "Param mismatch",
    }


@pytest.mark.parametrize(
    "category,status,message",
    [
        (chatbot_send_service.SENT, 200, "OK"),
        (chatbot_send_service.UNCONFIRMED, 202, "UNCONFIRMED"),
    ],
)
async def test_send_template_sent_and_unconfirmed_return_the_message(
    client, async_session_maker, monkeypatch, category, status, message
):
    """Guards AC3 (sent is 200 with the message; unknown is 202 in the ok envelope with
    message UNCONFIRMED) and AC4 (the returned message carries template_name)."""
    await _seed(async_session_maker)
    _mock_send(monkeypatch, SendReplyResult(category, message=_template_message()))
    response = await _request(client, REP, "post", SEND, json=_body())
    assert response.status_code == status
    assert response.json()["success"] is True
    assert response.json()["message"] == message
    data = response.json()["data"]
    assert (data["id"], data["template_name"], data["sent_by_name"]) == (
        "tmpl",
        "crm_followup",
        "Rep",
    )


async def test_send_template_end_to_end_through_the_service(
    client, async_session_maker, monkeypatch
):
    """Guards AC3 (the endpoint hands the request to CRM-TASK-097, which reads the
    whitelist, stores and sends; 200 returns the stored message with template_name)."""
    await _seed(async_session_maker, _inbound(datetime.utcnow() - timedelta(days=2)))
    calls = _chatbot(monkeypatch, (200, {"result": "sent", "wa_message_id": "wamid.T1"}))

    response = await _request(client, REP, "post", SEND, json=_body(params=[" Mei ", "Acuven"]))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["template_name"] == "crm_followup"
    assert data["body"] == "Hi Mei, Acuven would like to follow up."
    assert data["external_id"] == "wamid.T1"
    assert [path for path, _ in calls] == ["/internal/crm/templates", "/internal/crm/send-template"]
    assert calls[1][1]["template"] == {
        "name": "crm_followup",
        "language": "en",
        "params": ["Mei", "Acuven"],
    }


async def test_send_template_end_to_end_no_opt_in(client, async_session_maker, monkeypatch):
    """Guards AC3 (a contact who never wrote on WhatsApp is 409 NO_OPT_IN, chatbot not
    called)."""
    await _seed(async_session_maker)
    calls = _chatbot(monkeypatch, (200, {"result": "sent", "wa_message_id": "wamid.T1"}))
    response = await _request(client, REP, "post", SEND, json=_body())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_OPT_IN"
    assert calls == []


# --- AC4: template retry through the existing endpoint --------------------------


async def _seed_unconfirmed_template(maker):
    """A template message sent 30 minutes ago, never confirmed; the reply window has
    been closed for a day, which a template retry does not check."""
    now = datetime.utcnow()
    await _seed(
        maker,
        _inbound(now - timedelta(days=2)),
        _template_message(created_at=now - timedelta(minutes=30)),
    )


async def test_template_retry_sent_is_200(client, async_session_maker, monkeypatch):
    """Guards AC4 (the existing retry path retries a template message through
    send-template with its stored name, language and params; sent is 200 with
    template_name)."""
    await _seed_unconfirmed_template(async_session_maker)
    calls = _chatbot(monkeypatch, (200, {"result": "sent", "wa_message_id": "wamid.R1"}))

    response = await _request(client, REP, "post", "/api/messages/tmpl/whatsapp/retry")

    assert response.status_code == 200
    assert response.json()["message"] == "OK"
    data = response.json()["data"]
    assert (data["id"], data["template_name"], data["external_id"]) == (
        "tmpl",
        "crm_followup",
        "wamid.R1",
    )
    assert [path for path, _ in calls] == ["/internal/crm/send-template"]
    assert calls[0][1]["request_id"] == "tmpl"
    assert calls[0][1]["template"] == {
        "name": "crm_followup",
        "language": "en",
        "params": ["Mei", "Acuven"],
    }


async def test_template_retry_unknown_is_202(client, async_session_maker, monkeypatch):
    """Guards AC4 (a template retry with an unknown outcome is 202, ok envelope, message
    UNCONFIRMED, and returns the kept message with template_name)."""
    await _seed_unconfirmed_template(async_session_maker)
    _chatbot(monkeypatch, (500, {}))

    response = await _request(client, REP, "post", "/api/messages/tmpl/whatsapp/retry")

    assert response.status_code == 202
    assert response.json()["success"] is True
    assert response.json()["message"] == "UNCONFIRMED"
    assert response.json()["data"]["template_name"] == "crm_followup"


async def test_template_retry_send_failed_is_502(client, async_session_maker, monkeypatch):
    """Guards AC4 (a template retry uses the same mapping: explicitly not sent is 502
    SEND_FAILED with error_code and error_title)."""
    await _seed_unconfirmed_template(async_session_maker)
    _chatbot(monkeypatch, (502, {"result": "rejected", "error_code": 1, "error_title": "No"}))

    response = await _request(client, REP, "post", "/api/messages/tmpl/whatsapp/retry")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SEND_FAILED"
    assert response.json()["error"]["fields"] == {"error_code": 1, "error_title": "No"}


async def test_template_retry_keeps_the_retry_permission(client, async_session_maker, monkeypatch):
    """Guards AC4 (the retry endpoint's permission is unchanged: a user without access
    to the contact gets 403 NOT_ASSIGNED and chatbot is not called)."""
    await _seed_unconfirmed_template(async_session_maker)
    calls = _chatbot(monkeypatch, (200, {"result": "sent", "wa_message_id": "wamid.R1"}))
    response = await _request(client, OTHER, "post", "/api/messages/tmpl/whatsapp/retry")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"
    assert calls == []


# --- AC4: template_name on messages -----------------------------------------------


async def test_messages_carry_template_name(client, async_session_maker):
    """Guards AC4 (the message list, contact messages and conversation list return
    template_name on every message: the name for a template, empty for text)."""
    now = datetime.utcnow()
    await _seed(
        async_session_maker,
        Contact(id="contact2", name="Lim", phone="60199999999", assigned_to="rep"),
        _text_message("text", created_at=now - timedelta(minutes=5)),
        _template_message("tmpl", created_at=now - timedelta(minutes=1)),
        _text_message("text2", contact_id="contact2", created_at=now - timedelta(minutes=2)),
    )

    listing = await _request(client, ADMIN, "get", "/api/messages")
    listed = {item["id"]: item["template_name"] for item in listing.json()["data"]["data"]}
    assert listed == {"text": None, "tmpl": "crm_followup", "text2": None}

    contact = await _request(client, REP, "get", "/api/messages/contact/contact")
    assert [(item["id"], item["template_name"]) for item in contact.json()["data"]] == [
        ("text", None),
        ("tmpl", "crm_followup"),
    ]

    conversations = await _request(client, REP, "get", "/api/messages/conversations")
    rows = {item["id"]: item["template_name"] for item in conversations.json()["data"]["data"]}
    assert rows == {"tmpl": "crm_followup", "text2": None}


async def test_text_send_returns_empty_template_name(client, async_session_maker, monkeypatch):
    """Guards AC4 (the text send endpoint's message also carries template_name, empty)."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        chatbot_send_service,
        "send_agent_reply",
        AsyncMock(return_value=SendReplyResult(chatbot_send_service.SENT, _text_message())),
    )
    response = await _request(
        client,
        REP,
        "post",
        "/api/messages/whatsapp/send",
        json={"contact_id": "contact", "message": "hi"},
    )
    assert response.status_code == 200
    assert "template_name" in response.json()["data"]
    assert response.json()["data"]["template_name"] is None
