"""CRM-TASK-052 API mappings for extracting contact details from chats."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.user import User
from app.services import ai_assist_service, contact_extract_service


def _user(user_id: str, role: str = "sales", language: str = "en") -> User:
    return User(
        id=user_id,
        name=user_id,
        email=f"{user_id}@example.com",
        password_hash="x",
        role=role,
        language=language,
    )


async def _seed(maker):
    async with maker() as session:
        session.add_all(
            [
                _user("rep"),
                _user("other"),
                Contact(id="contact", name="Contact", assigned_to="rep"),
                Contact(
                    id="deleted",
                    name="Deleted",
                    assigned_to="rep",
                    deleted_at=datetime(2026, 10, 1),
                ),
            ]
        )
        await session.commit()


async def _request(client, user, path="/api/ai/contacts/contact/extract", **kwargs):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.post(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _stub(monkeypatch, result=None):
    mocked = AsyncMock(
        return_value=result or ai_assist_service.AiAssistResult(ai_assist_service.OK)
    )
    monkeypatch.setattr(contact_extract_service, "generate_contact_extract", mocked)
    return mocked


async def test_extract_rejects_unsupported_language_without_calling_business_logic(
    client, async_session_maker, monkeypatch
):
    """Guards criterion 1: output_language outside zh-CN, en, ms returns 422 and skips business logic."""
    await _seed(async_session_maker)
    mocked = _stub(monkeypatch)
    response = await _request(client, _user("rep"), json={"output_language": "fr"})
    assert response.status_code == 422
    mocked.assert_not_awaited()


@pytest.mark.parametrize("language,expected", [("zh", "zh-CN"), ("en", "en")])
async def test_extract_defaults_to_current_user_interface_language(
    client, async_session_maker, monkeypatch, language, expected
):
    """Guards criterion 1: an omitted output_language follows the user's interface language like the brief endpoint."""
    await _seed(async_session_maker)
    mocked = _stub(monkeypatch)
    response = await _request(client, _user("rep", language=language), json={})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


@pytest.mark.parametrize("output_language", ["zh-CN", "en", "ms"])
async def test_extract_passes_explicit_language_unchanged(
    client, async_session_maker, monkeypatch, output_language
):
    """Guards criteria 1 and 3: an explicit output_language reaches business logic unchanged."""
    await _seed(async_session_maker)
    mocked = _stub(monkeypatch)
    response = await _request(
        client, _user("rep", language="zh"), json={"output_language": output_language}
    )
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == output_language


@pytest.mark.parametrize(
    "user,path",
    [
        (_user("other"), "/api/ai/contacts/contact/extract"),
        (_user("admin", role="admin"), "/api/ai/contacts/missing/extract"),
        (_user("admin", role="admin"), "/api/ai/contacts/deleted/extract"),
    ],
)
async def test_extract_hides_unassigned_missing_and_deleted_contacts(
    client, async_session_maker, monkeypatch, user, path
):
    """Guards criterion 2: unassigned, missing, or deleted contacts return 403 NOT_ASSIGNED without business logic."""
    await _seed(async_session_maker)
    mocked = _stub(monkeypatch)
    response = await _request(client, user, path=path, json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"
    mocked.assert_not_awaited()


async def test_extract_requires_login(client, async_session_maker, monkeypatch):
    """Guards criterion 2: the extract endpoint requires login."""
    await _seed(async_session_maker)
    mocked = _stub(monkeypatch)
    response = await client.post("/api/ai/contacts/contact/extract", json={})
    assert response.status_code in {401, 403}
    mocked.assert_not_awaited()


async def test_extract_passes_current_user_and_contact_id(client, async_session_maker, monkeypatch):
    """Guards criterion 3: business logic receives the requesting user object and the path contact_id."""
    await _seed(async_session_maker)
    mocked = _stub(monkeypatch)
    rep = _user("rep")
    response = await _request(client, rep, json={})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["user"] is rep
    assert mocked.await_args.kwargs["contact_id"] == "contact"


async def test_extract_success_returns_the_extract_contract(
    client, async_session_maker, monkeypatch
):
    """Guards criterion 3: ok returns 200 with request_id, suggestions, and truncated."""
    await _seed(async_session_maker)
    suggestions = [
        {"field": "email", "value": "a@b.com", "evidence": "a@b.com", "message_index": 0}
    ]
    _stub(
        monkeypatch,
        ai_assist_service.AiAssistResult(
            ai_assist_service.OK,
            request_id="extract-id",
            suggestions=suggestions,
            truncated=True,
        ),
    )
    response = await _request(client, _user("rep"), json={})
    assert response.status_code == 200
    assert response.json()["data"] == {
        "request_id": "extract-id",
        "suggestions": suggestions,
        "truncated": True,
    }


@pytest.mark.parametrize(
    "category,status,code",
    [
        (ai_assist_service.NOTHING_TO_FILL, 400, "NOTHING_TO_FILL"),
        (ai_assist_service.NO_MESSAGES, 400, "NO_MESSAGES"),
        (ai_assist_service.NOT_CONFIGURED, 503, "AI_NOT_CONFIGURED"),
        (ai_assist_service.UNAVAILABLE, 503, "AI_UNAVAILABLE"),
        (ai_assist_service.ERROR, 502, "AI_ERROR"),
        (ai_assist_service.INVALID_INPUT, 422, "INVALID_INPUT"),
    ],
)
async def test_extract_maps_every_business_failure(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards criterion 3: each non-ok business category maps to its specified HTTP status and error code."""
    await _seed(async_session_maker)
    _stub(monkeypatch, ai_assist_service.AiAssistResult(category))
    response = await _request(client, _user("rep"), json={})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
