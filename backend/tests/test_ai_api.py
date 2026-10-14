"""CRM-TASK-024 API mappings for inbox AI assistance."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.user import User
from app.services import ai_api_client, ai_assist_service

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="other", name="Other", email="other@example.com", password_hash="x", role="sales")


async def _seed(maker, *, deleted: bool = False):
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
    "path,payload",
    [
        ("/api/ai/conversations/contact/brief", {"output_language": "fr"}),
        ("/api/ai/conversations/contact/reply-draft", {"channel": "sms"}),
        ("/api/ai/conversations/contact/reply-draft", {"channel": "email", "instruction": " "}),
        (
            "/api/ai/conversations/contact/reply-draft",
            {"channel": "email", "instruction": "x" * 201},
        ),
        ("/api/ai/translate", {"contact_id": "contact", "text": " ", "target_language": "en"}),
        (
            "/api/ai/translate",
            {"contact_id": "contact", "text": "x" * 2001, "target_language": "en"},
        ),
        ("/api/ai/translate", {"contact_id": "contact", "text": "x", "target_language": "fr"}),
    ],
)
async def test_request_models_reject_invalid_input(client, async_session_maker, path, payload):
    """Guards the acceptance criterion that invalid AI request fields return 422."""
    await _seed(async_session_maker)
    response = await _request(client, REP, "post", path, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path,payload,service_name",
    [
        ("/api/ai/conversations/missing/brief", {}, "generate_conversation_brief"),
        ("/api/ai/conversations/contact/reply-draft", {"channel": "email"}, "generate_reply_draft"),
        (
            "/api/ai/translate",
            {"contact_id": "contact", "text": "hello", "target_language": "en"},
            "translate_text",
        ),
    ],
)
async def test_permission_refusal_hides_missing_and_unassigned_contacts(
    client, async_session_maker, monkeypatch, path, payload, service_name
):
    """Guards the acceptance criterion that denied AI requests are one 403 and never call business logic."""
    await _seed(async_session_maker)
    mocked = AsyncMock()
    monkeypatch.setattr(ai_assist_service, service_name, mocked)
    user = REP if "missing" in path else OTHER
    response = await _request(client, user, "post", path, json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"
    mocked.assert_not_awaited()


async def test_deleted_contact_is_hidden_from_admin(client, async_session_maker, monkeypatch):
    """Guards the acceptance criterion that an admin also gets NOT_ASSIGNED for a deleted contact."""
    await _seed(async_session_maker, deleted=True)
    mocked = AsyncMock()
    monkeypatch.setattr(ai_assist_service, "generate_conversation_brief", mocked)
    response = await _request(client, ADMIN, "post", "/api/ai/conversations/contact/brief", json={})
    assert response.status_code == 403
    mocked.assert_not_awaited()


@pytest.mark.parametrize(
    "path,payload,service_name,language,expected",
    [
        ("/api/ai/conversations/contact/brief", {}, "generate_conversation_brief", "zh", "zh-CN"),
        (
            "/api/ai/conversations/contact/reply-draft",
            {"channel": "email"},
            "generate_reply_draft",
            "en",
            "en",
        ),
    ],
)
async def test_generation_defaults_to_current_user_language(
    client, async_session_maker, monkeypatch, path, payload, service_name, language, expected
):
    """Guards the acceptance criterion that omitted output language follows the current user's UI language."""
    await _seed(async_session_maker)
    REP.language = language
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(ai_assist_service, service_name, mocked)
    response = await _request(client, REP, "post", path, json=payload)
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


@pytest.mark.parametrize(
    "path,payload,service_name,category,status,code",
    [
        (
            "/api/ai/conversations/contact/brief",
            {},
            "generate_conversation_brief",
            ai_assist_service.NOT_CONFIGURED,
            503,
            "AI_NOT_CONFIGURED",
        ),
        (
            "/api/ai/conversations/contact/reply-draft",
            {"channel": "email"},
            "generate_reply_draft",
            ai_assist_service.UNAVAILABLE,
            503,
            "AI_UNAVAILABLE",
        ),
        (
            "/api/ai/translate",
            {"contact_id": "contact", "text": "hello", "target_language": "en"},
            "translate_text",
            ai_assist_service.ERROR,
            502,
            "AI_ERROR",
        ),
        (
            "/api/ai/conversations/contact/brief",
            {},
            "generate_conversation_brief",
            ai_assist_service.NO_MESSAGES,
            400,
            "NO_MESSAGES",
        ),
        (
            "/api/ai/conversations/contact/brief",
            {},
            "generate_conversation_brief",
            ai_assist_service.INVALID_INPUT,
            422,
            "INVALID_INPUT",
        ),
    ],
)
async def test_routes_map_every_business_failure(
    client, async_session_maker, monkeypatch, path, payload, service_name, category, status, code
):
    """Guards the acceptance criterion that every specified AI business result has its HTTP mapping."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        ai_assist_service,
        service_name,
        AsyncMock(return_value=ai_assist_service.AiAssistResult(category)),
    )
    response = await _request(client, REP, "post", path, json=payload)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_success_responses_follow_the_three_contracts(
    client, async_session_maker, monkeypatch
):
    """Guards the acceptance criterion that successful responses expose all design section 4.3 fields."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        ai_assist_service,
        "generate_conversation_brief",
        AsyncMock(
            return_value=ai_assist_service.AiAssistResult(
                ai_assist_service.OK,
                request_id="brief-id",
                customer_need="need",
                conversation_state="open",
                customer_mood="neutral",
                open_questions=["question"],
                suggested_next_step="reply",
                truncated=True,
            )
        ),
    )
    monkeypatch.setattr(
        ai_assist_service,
        "generate_reply_draft",
        AsyncMock(
            return_value=ai_assist_service.AiAssistResult(
                ai_assist_service.OK,
                request_id="draft-id",
                draft="draft",
                subject=None,
                note_for_agent="note",
            )
        ),
    )
    monkeypatch.setattr(
        ai_assist_service,
        "translate_text",
        AsyncMock(
            return_value=ai_assist_service.AiAssistResult(
                ai_assist_service.OK,
                request_id="translation-id",
                translation="译文",
                detected_language="en",
            )
        ),
    )
    brief = await _request(client, REP, "post", "/api/ai/conversations/contact/brief", json={})
    draft = await _request(
        client,
        REP,
        "post",
        "/api/ai/conversations/contact/reply-draft",
        json={"channel": "whatsapp"},
    )
    translation = await _request(
        client,
        REP,
        "post",
        "/api/ai/translate",
        json={"contact_id": "contact", "text": "hello", "target_language": "zh-CN"},
    )
    assert brief.json()["data"]["truncated"] is True
    assert draft.json()["data"] == {
        "request_id": "draft-id",
        "draft": "draft",
        "subject": None,
        "note_for_agent": "note",
    }
    assert translation.json()["data"] == {
        "request_id": "translation-id",
        "translation": "译文",
        "detected_language": "en",
    }


@pytest.mark.parametrize("enabled", [False, True])
async def test_status_requires_login_and_reveals_only_enabled(
    client, async_session_maker, monkeypatch, enabled
):
    """Guards the acceptance criterion that status is authenticated and does not expose configuration values."""
    await _seed(async_session_maker)
    assert (await client.get("/api/ai/status")).status_code in {401, 403}
    monkeypatch.setattr(ai_api_client, "is_ai_configured", lambda: enabled)
    response = await _request(client, REP, "get", "/api/ai/status")
    assert response.json()["data"] == {"enabled": enabled}
