"""CRM-TASK-043 API mappings for the manager dashboard AI insight."""

from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import User
from app.services import ai_assist_service, dashboard_insight_service

PATH = "/api/ai/dashboard/insight"


def _user(role: str, language: str = "en") -> User:
    return User(
        id=role,
        name=role.title(),
        email=f"{role}@example.com",
        password_hash="x",
        role=role,
        language=language,
    )


async def _request(client, user, **kwargs):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.post(PATH, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _patch(monkeypatch, category=ai_assist_service.OK, **fields):
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(category, **fields))
    monkeypatch.setattr(dashboard_insight_service, "generate_dashboard_insight", mocked)
    return mocked


async def test_insight_rejects_unsupported_language_without_calling_business_logic(
    client, async_session_maker, monkeypatch
):
    """Guards test case 1: output_language fr returns 422 and the business function is not called."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user("manager"), json={"output_language": "fr"})
    assert response.status_code == 422
    mocked.assert_not_awaited()


@pytest.mark.parametrize(
    "language,payload,expected",
    [
        ("zh", {}, "zh-CN"),
        ("en", {}, "en"),
        ("zh", {"output_language": "en"}, "en"),
        ("en", {"output_language": "zh-CN"}, "zh-CN"),
        ("en", {"output_language": "ms"}, "ms"),
    ],
)
async def test_insight_output_language_defaults_to_interface_language_or_passes_explicit(
    client, async_session_maker, monkeypatch, language, payload, expected
):
    """Guards test case 2: omitted language follows the manager's interface language (zh to zh-CN, else en); explicit values pass through unchanged."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user("manager", language), json=payload)
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


@pytest.mark.parametrize("role", ["sales", "admin"])
async def test_insight_forbids_non_managers_without_calling_business_logic(
    client, async_session_maker, monkeypatch, role
):
    """Guards test case 3: sales and admin requests get require_role's 403 and the business function is not called."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user(role), json={})
    assert response.status_code == 403
    mocked.assert_not_awaited()


async def test_insight_requires_login_without_calling_business_logic(
    client, async_session_maker, monkeypatch
):
    """Guards test case 3: unauthenticated requests return 401 or 403 (design section 9 item 2) without calling the business function."""
    mocked = _patch(monkeypatch)
    response = await client.post(PATH, json={})
    assert response.status_code in {401, 403}
    mocked.assert_not_awaited()


async def test_insight_manager_request_calls_business_logic_with_current_user(
    client, async_session_maker, monkeypatch
):
    """Guards test cases 3 and 4: a manager request calls the business function, which receives the current manager."""
    manager = _user("manager")
    mocked = _patch(monkeypatch)
    response = await _request(client, manager, json={})
    assert response.status_code == 200
    mocked.assert_awaited_once()
    assert mocked.await_args.kwargs["user"] is manager


async def test_insight_success_returns_the_five_fields(client, async_session_maker, monkeypatch):
    """Guards test case 5: ok returns 200 with request_id, headline, observations, suggestions and truncated."""
    _patch(
        monkeypatch,
        request_id="insight-id",
        headline="Headline",
        observations=["One", "Two"],
        suggestions=["Do this"],
        truncated=False,
    )
    response = await _request(client, _user("manager"), json={})
    assert response.status_code == 200
    assert response.json()["data"] == {
        "request_id": "insight-id",
        "headline": "Headline",
        "observations": ["One", "Two"],
        "suggestions": ["Do this"],
        "truncated": False,
    }


@pytest.mark.parametrize(
    "category,status,code",
    [
        (ai_assist_service.NO_DATA, 400, "NO_DATA"),
        (ai_assist_service.NOT_CONFIGURED, 503, "AI_NOT_CONFIGURED"),
        (ai_assist_service.UNAVAILABLE, 503, "AI_UNAVAILABLE"),
        (ai_assist_service.ERROR, 502, "AI_ERROR"),
        (ai_assist_service.INVALID_INPUT, 422, "INVALID_INPUT"),
    ],
)
async def test_insight_maps_every_business_failure(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards test case 5: no_data, not_configured, unavailable, error and invalid_input map to their status codes and error codes."""
    _patch(monkeypatch, category)
    response = await _request(client, _user("manager"), json={})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
