"""CRM-TASK-033 API mappings for follow-up AI drafts."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.user import User
from app.services import ai_assist_service, followup_draft_service

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="other", name="Other", email="other@example.com", password_hash="x", role="sales")
PATH = "/api/ai/contacts/contact/followup-draft"
PAYLOAD = {"deal_id": "deal", "note": "Call completed", "today": "2026-10-15"}


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


async def _request(client, user, path=PATH, payload=PAYLOAD):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.post(path, json=payload)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    "payload",
    [
        {"note": "note", "today": "2026-10-15"},
        {"deal_id": "deal", "today": "2026-10-15"},
        {"deal_id": "deal", "note": "note"},
        {"deal_id": "deal", "note": "   ", "today": "2026-10-15"},
        {"deal_id": "deal", "note": "x" * 2001, "today": "2026-10-15"},
        {"deal_id": "deal", "note": "note", "today": "2026/10/15"},
        {"deal_id": "deal", "note": "note", "today": "2026-10-15", "output_language": "fr"},
    ],
)
async def test_request_model_rejects_invalid_input_without_calling_service(
    client, async_session_maker, monkeypatch, payload
):
    """Guards CRM-TASK-033 validation acceptance: invalid fields return 422 before business logic."""
    await _seed(async_session_maker)
    mocked = AsyncMock()
    monkeypatch.setattr(followup_draft_service, "generate_followup_draft", mocked)
    response = await _request(client, REP, payload=payload)
    assert response.status_code == 422
    mocked.assert_not_awaited()


@pytest.mark.parametrize(
    "language,payload,expected",
    [
        ("zh", PAYLOAD, "zh-CN"),
        ("en", PAYLOAD, "en"),
        ("ms", {**PAYLOAD, "output_language": "ms"}, "ms"),
    ],
)
async def test_output_language_uses_ui_default_or_explicit_value(
    client, async_session_maker, monkeypatch, language, payload, expected
):
    """Guards CRM-TASK-033 language acceptance: defaults match UI language and explicit values pass through."""
    await _seed(async_session_maker)
    user = User(
        id="rep",
        name="Rep",
        email="rep@example.com",
        password_hash="x",
        role="sales",
        language=language,
    )
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(followup_draft_service, "generate_followup_draft", mocked)
    response = await _request(client, user, payload=payload)
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


@pytest.mark.parametrize(
    "user,path,deleted",
    [
        (OTHER, PATH, False),
        (ADMIN, "/api/ai/contacts/missing/followup-draft", False),
        (ADMIN, PATH, True),
    ],
)
async def test_inaccessible_contacts_are_hidden_without_calling_service(
    client, async_session_maker, monkeypatch, user, path, deleted
):
    """Guards CRM-TASK-033 permission acceptance: all inaccessible contacts are NOT_ASSIGNED without business logic."""
    await _seed(async_session_maker, deleted=deleted)
    mocked = AsyncMock()
    monkeypatch.setattr(followup_draft_service, "generate_followup_draft", mocked)
    response = await _request(client, user, path=path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"
    mocked.assert_not_awaited()


async def test_route_forwards_the_current_user_and_request_fields(
    client, async_session_maker, monkeypatch
):
    """Guards CRM-TASK-033 forwarding acceptance: business logic receives every required argument unchanged."""
    await _seed(async_session_maker)
    payload = {"deal_id": "deal-id", "note": "  raw note  ", "today": "2026-10-15"}
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(followup_draft_service, "generate_followup_draft", mocked)
    response = await _request(client, REP, payload=payload)
    assert response.status_code == 200
    assert len(mocked.await_args.args) == 1
    assert mocked.await_args.kwargs == {
        "user": REP,
        "contact_id": "contact",
        "deal_id": "deal-id",
        "note": "  raw note  ",
        "today": "2026-10-15",
        "output_language": "en",
    }


@pytest.mark.parametrize(
    "result,status,code",
    [
        (
            ai_assist_service.AiAssistResult(
                ai_assist_service.OK,
                request_id="request-id",
                activity_type="call",
                content="content",
                tasks=[],
            ),
            200,
            None,
        ),
        (
            ai_assist_service.AiAssistResult(ai_assist_service.NOT_CONFIGURED),
            503,
            "AI_NOT_CONFIGURED",
        ),
        (ai_assist_service.AiAssistResult(ai_assist_service.UNAVAILABLE), 503, "AI_UNAVAILABLE"),
        (ai_assist_service.AiAssistResult(ai_assist_service.ERROR), 502, "AI_ERROR"),
        (ai_assist_service.AiAssistResult(ai_assist_service.INVALID_INPUT), 422, "INVALID_INPUT"),
    ],
)
async def test_route_maps_followup_draft_results(
    client, async_session_maker, monkeypatch, result, status, code
):
    """Guards CRM-TASK-033 mapping acceptance: result categories use the established AI response shell."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        followup_draft_service, "generate_followup_draft", AsyncMock(return_value=result)
    )
    response = await _request(client, REP)
    assert response.status_code == status
    if code is None:
        assert response.json()["data"] == {
            "request_id": "request-id",
            "activity_type": "call",
            "content": "content",
            "tasks": [],
        }
    else:
        assert response.json()["error"]["code"] == code
