"""CRM-TASK-028 API mappings for contact AI summaries."""

from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.user import User
from app.services import ai_assist_service, contact_summary_service

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="other", name="Other", email="other@example.com", password_hash="x", role="sales")


async def _seed(maker):
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
                Contact(id="contact", name="Contact", assigned_to="rep"),
            ]
        )
        await session.commit()


async def _request(client, user, **kwargs):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.post("/api/ai/contacts/contact/summary", **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_summary_requires_login(client, async_session_maker):
    """Guards the acceptance criterion that the contact summary endpoint requires login."""
    await _seed(async_session_maker)
    response = await client.post("/api/ai/contacts/contact/summary", json={})
    assert response.status_code in {401, 403}


@pytest.mark.parametrize("payload", [{"output_language": "fr"}, {"output_language": 1}])
async def test_summary_request_model_rejects_unsupported_language(
    client, async_session_maker, payload
):
    """Guards the acceptance criterion that only zh-CN, en, and ms are valid output languages."""
    await _seed(async_session_maker)
    response = await _request(client, REP, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "user,path",
    [(OTHER, "/api/ai/contacts/contact/summary"), (ADMIN, "/api/ai/contacts/missing/summary")],
)
async def test_summary_hides_unassigned_and_missing_contacts_without_calling_business_logic(
    client, async_session_maker, monkeypatch, user, path
):
    """Guards the acceptance criterion that unassigned and missing contacts both return 403 without audit-producing business logic."""
    await _seed(async_session_maker)
    mocked = AsyncMock()
    monkeypatch.setattr(contact_summary_service, "generate_contact_summary", mocked)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = await client.post(path, json={})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"
    mocked.assert_not_awaited()


@pytest.mark.parametrize("language,expected", [("zh", "zh-CN"), ("en", "en")])
async def test_summary_defaults_to_current_user_interface_language(
    client, async_session_maker, monkeypatch, language, expected
):
    """Guards the acceptance criterion that omitted output language follows the current user's interface language."""
    await _seed(async_session_maker)
    REP.language = language
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(contact_summary_service, "generate_contact_summary", mocked)
    response = await _request(client, REP, json={})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


@pytest.mark.parametrize("output_language", ["zh-CN", "en", "ms"])
async def test_summary_passes_current_user_and_valid_language_to_business_logic(
    client, async_session_maker, monkeypatch, output_language
):
    """Guards the acceptance criterion that all allowed languages and the current user reach business logic."""
    await _seed(async_session_maker)
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(contact_summary_service, "generate_contact_summary", mocked)
    response = await _request(client, REP, json={"output_language": output_language})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["user"] is REP
    assert mocked.await_args.kwargs["output_language"] == output_language


@pytest.mark.parametrize(
    "category,status,code",
    [
        (ai_assist_service.NO_HISTORY, 400, "NO_HISTORY"),
        (ai_assist_service.NOT_CONFIGURED, 503, "AI_NOT_CONFIGURED"),
        (ai_assist_service.UNAVAILABLE, 503, "AI_UNAVAILABLE"),
        (ai_assist_service.ERROR, 502, "AI_ERROR"),
        (ai_assist_service.INVALID_INPUT, 422, "INVALID_INPUT"),
    ],
)
async def test_summary_maps_every_business_failure(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards the acceptance criterion that every contact summary business result uses its specified HTTP error."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        contact_summary_service,
        "generate_contact_summary",
        AsyncMock(return_value=ai_assist_service.AiAssistResult(category)),
    )
    response = await _request(client, REP, json={})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_summary_success_returns_the_contact_summary_contract(
    client, async_session_maker, monkeypatch
):
    """Guards the acceptance criterion that successful summaries return all four response fields."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        contact_summary_service,
        "generate_contact_summary",
        AsyncMock(
            return_value=ai_assist_service.AiAssistResult(
                ai_assist_service.OK,
                request_id="summary-id",
                summary="Summary",
                suggested_next_step="Follow up",
                truncated=True,
            )
        ),
    )
    response = await _request(client, REP, json={})
    assert response.status_code == 200
    assert response.json()["data"] == {
        "request_id": "summary-id",
        "summary": "Summary",
        "suggested_next_step": "Follow up",
        "truncated": True,
    }
