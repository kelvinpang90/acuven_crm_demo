"""CRM-TASK-057 API mappings for AI column recognition in spreadsheet import."""

from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.user import User
from app.services import ai_assist_service, import_columns_service

PATH = "/api/ai/import/columns"
COLUMNS = [
    {"header": "手机", "samples": ["012-345 6789", "0198765432"]},
    {"header": "Company Name", "samples": ["Tan Hardware"]},
]


def _user(language: str = "en") -> User:
    return User(
        id="sales",
        name="Sales",
        email="sales@example.com",
        password_hash="x",
        role="sales",
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
    monkeypatch.setattr(import_columns_service, "suggest_import_columns", mocked)
    return mocked


async def test_import_columns_requires_login_without_calling_business_logic(
    client, async_session_maker, monkeypatch
):
    """Guards test case 1: unauthenticated requests return 401 or 403 and the business function is not called."""
    mocked = _patch(monkeypatch)
    response = await client.post(PATH, json={"columns": COLUMNS})
    assert response.status_code in {401, 403}
    mocked.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"output_language": "en"},
        {"columns": "手机"},
        {"columns": [{"samples": ["a"]}]},
        {"columns": [{"header": "手机"}]},
        {"columns": [{"header": 1, "samples": ["a"]}]},
        {"columns": [{"header": "手机", "samples": "a"}]},
        {"columns": [{"header": "手机", "samples": [1]}]},
        {"columns": COLUMNS, "output_language": "fr"},
    ],
)
async def test_import_columns_rejects_missing_or_mistyped_fields_without_calling_business_logic(
    client, async_session_maker, monkeypatch, payload
):
    """Guards test case 2: missing or mistyped columns (and an unsupported output_language) return 422 and the business function is not called."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user(), json=payload)
    assert response.status_code == 422
    mocked.assert_not_awaited()


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
async def test_import_columns_output_language_defaults_to_interface_language_or_passes_explicit(
    client, async_session_maker, monkeypatch, language, extra, expected
):
    """Guards test case 3: omitted language follows the interface language (zh to zh-CN, else en); explicit values pass through unchanged."""
    mocked = _patch(monkeypatch)
    response = await _request(client, _user(language), json={"columns": COLUMNS, **extra})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


async def test_import_columns_passes_current_user_columns_and_language(
    client, async_session_maker, monkeypatch
):
    """Guards test case 4: the business function receives the current user, the columns as sent and the output language."""
    user = _user()
    mocked = _patch(monkeypatch)
    response = await _request(client, user, json={"columns": COLUMNS, "output_language": "ms"})
    assert response.status_code == 200
    mocked.assert_awaited_once()
    kwargs = mocked.await_args.kwargs
    assert kwargs["user"] is user
    assert kwargs["columns"] == COLUMNS
    assert kwargs["output_language"] == "ms"


async def test_import_columns_success_returns_request_id_and_mappings(
    client, async_session_maker, monkeypatch
):
    """Guards test case 5: ok returns 200 with request_id and mappings."""
    mappings = [
        {"header": "手机", "field": "phone"},
        {"header": "Company Name", "field": "company"},
    ]
    _patch(monkeypatch, request_id="columns-id", mappings=mappings)
    response = await _request(client, _user(), json={"columns": COLUMNS})
    assert response.status_code == 200
    assert response.json()["data"] == {"request_id": "columns-id", "mappings": mappings}


@pytest.mark.parametrize(
    "category,status,code",
    [
        (ai_assist_service.NOT_CONFIGURED, 503, "AI_NOT_CONFIGURED"),
        (ai_assist_service.UNAVAILABLE, 503, "AI_UNAVAILABLE"),
        (ai_assist_service.ERROR, 502, "AI_ERROR"),
        (ai_assist_service.INVALID_INPUT, 422, "INVALID_INPUT"),
    ],
)
async def test_import_columns_maps_every_business_failure(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards test case 5: not_configured, unavailable, error and invalid_input map to their status codes and error codes."""
    _patch(monkeypatch, category)
    response = await _request(client, _user(), json={"columns": COLUMNS})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
