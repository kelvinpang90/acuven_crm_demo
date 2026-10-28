"""CRM-TASK-083 API mappings for AI roofing project report drafts."""

from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import User
from app.services import ai_assist_service, project_report_service

PATH = "/api/ai/projects/project-1/report"
PAYLOAD = {"report_type": "inspection", "utc_offset_minutes": 480}


def _user(role: str = "sales", language: str = "en") -> User:
    return User(
        id=role,
        name=role.title(),
        email=f"{role}@example.com",
        password_hash="x",
        role=role,
        language=language,
    )


async def _request(client, user, payload=PAYLOAD):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.post(PATH, json=payload)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _patch(monkeypatch, category=ai_assist_service.OK, **fields):
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(category, **fields))
    monkeypatch.setattr(project_report_service, "generate_project_report", mocked)
    return mocked


@pytest.mark.parametrize(
    "payload",
    [
        {"utc_offset_minutes": 480},
        {"report_type": "progress", "utc_offset_minutes": 480},
        {"report_type": "inspection"},
        {"report_type": "inspection", "utc_offset_minutes": 841},
        {"report_type": "inspection", "utc_offset_minutes": -721},
        {**PAYLOAD, "output_language": "fr"},
    ],
)
async def test_request_model_rejects_invalid_fields_without_calling_service(
    client, async_session_maker, monkeypatch, payload
):
    """Guards test case 1 (request model criterion): invalid report_type, utc_offset_minutes or output_language return 422 without business logic."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user(), payload)
    assert response.status_code == 422
    mocked.assert_not_awaited()


async def test_omitted_notes_are_passed_as_null(client, async_session_maker, monkeypatch):
    """Guards test case 1 (request model criterion): notes is optional and passed as null when omitted."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user())
    assert response.status_code == 200
    assert mocked.await_args.kwargs["notes"] is None


@pytest.mark.parametrize(
    "language,extra,expected",
    [
        ("zh", {}, "zh-CN"),
        ("en", {}, "en"),
        ("zh", {"output_language": "en"}, "en"),
        ("en", {"output_language": "zh-CN"}, "zh-CN"),
        ("en", {"output_language": "ms"}, "ms"),
    ],
)
async def test_output_language_defaults_to_interface_language_or_passes_explicit(
    client, async_session_maker, monkeypatch, language, extra, expected
):
    """Guards test case 2 (request model criterion): omitted output_language follows the interface language; explicit values pass through."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user(language=language), {**PAYLOAD, **extra})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


async def test_invalid_credentials_return_401_without_calling_service(
    client, async_session_maker, monkeypatch
):
    """Guards test case 3 (login criterion): a request that is not logged in returns 401 without business logic."""
    mocked = _patch(monkeypatch)
    response = await client.post(
        PATH, json=PAYLOAD, headers={"Authorization": "Bearer not-a-valid-token"}
    )
    assert response.status_code == 401
    mocked.assert_not_awaited()


@pytest.mark.parametrize("role", ["sales", "admin"])
async def test_sales_and_admin_may_call(client, async_session_maker, monkeypatch, role):
    """Guards test case 3 (login criterion): any logged-in user may call, regardless of role."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user(role))
    assert response.status_code == 200
    mocked.assert_awaited_once()


async def test_route_forwards_current_user_and_request_fields(
    client, async_session_maker, monkeypatch
):
    """Guards test case 4 (forwarding criterion): the business function receives the user, project_id and every request field unchanged."""
    user = _user()
    mocked = _patch(monkeypatch)
    payload = {
        "report_type": "completion",
        "notes": "Gutter rusted for about 4 m",
        "utc_offset_minutes": -300,
        "output_language": "ms",
    }
    response = await _request(client, user, payload)
    assert response.status_code == 200
    mocked.assert_awaited_once()
    assert len(mocked.await_args.args) == 1
    assert mocked.await_args.kwargs == {
        "user": user,
        "project_id": "project-1",
        "report_type": "completion",
        "notes": "Gutter rusted for about 4 m",
        "utc_offset_minutes": -300,
        "output_language": "ms",
    }


async def test_success_returns_request_id_title_body_and_note(
    client, async_session_maker, monkeypatch
):
    """Guards test case 5 (mapping criterion): ok returns 200 with request_id, title, body and note_for_agent."""
    _patch(
        monkeypatch,
        request_id="report-id",
        title="Inspection report",
        body="Dear Lim Bungalow, ...",
        note_for_agent="Attach the photos.",
    )
    response = await _request(client, _user())
    assert response.status_code == 200
    assert response.json()["data"] == {
        "request_id": "report-id",
        "title": "Inspection report",
        "body": "Dear Lim Bungalow, ...",
        "note_for_agent": "Attach the photos.",
    }


@pytest.mark.parametrize(
    "category,status,code",
    [
        (ai_assist_service.NOT_FOUND, 404, "NOT_FOUND"),
        (ai_assist_service.STEP_NOT_REACHED, 400, "STEP_NOT_REACHED"),
        (ai_assist_service.NOT_CONFIGURED, 503, "AI_NOT_CONFIGURED"),
        (ai_assist_service.UNAVAILABLE, 503, "AI_UNAVAILABLE"),
        (ai_assist_service.ERROR, 502, "AI_ERROR"),
        (ai_assist_service.INVALID_INPUT, 422, "INVALID_INPUT"),
    ],
)
async def test_route_maps_every_business_failure(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards test case 5 (mapping criterion): each failure category maps to its status code and error code in the fail shell."""
    _patch(monkeypatch, category)
    response = await _request(client, _user())
    assert response.status_code == status
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == code
