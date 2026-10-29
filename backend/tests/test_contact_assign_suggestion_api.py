"""POST /api/contacts/assign-suggestion (docs/contact-assign-suggest.md sections
3, 4 and 9; CRM-TASK-088). The business function is stubbed throughout."""

from unittest.mock import AsyncMock

import pytest
from starlette.routing import Match

from app.main import app
from app.routers import contacts
from app.services import routing_service
from tests._people import ADMIN, BOSS, REP, logged_in_as

PATH = "/api/contacts/assign-suggestion"

RESULT = {
    "rule_pick": {
        "user_id": "u-rep",
        "user_name": "Rep",
        "rule_name": "KL region",
        "fallback": False,
        "selectable": True,
    },
    "candidates": [
        {
            "user_id": "u-rep",
            "name": "Rep",
            "contact_count": 3,
            "open_deal_count": 2,
            "won_90d": 1,
            "total_90d": 4,
            "industry_contact_count": 1,
            "industry_won_count": 0,
        }
    ],
}


def _patch(monkeypatch) -> AsyncMock:
    mocked = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(routing_service, "suggest_assignee", mocked)
    return mocked


@pytest.mark.parametrize(
    "field, value",
    [
        ("name", "x" * 101),
        ("company", "x" * 201),
        ("industry", "x" * 51),
        ("address", "x" * 1001),
        ("name", 1),
        ("company", 1),
        ("industry", 1),
        ("address", 1),
    ],
)
async def test_invalid_field_is_422_without_calling_service(
    client, async_session_maker, monkeypatch, field, value
):
    """Acceptance 1 (AssignSuggestionRequest caps name/company/industry/address at
    100/200/50/1000 and takes only strings or null): over the cap or a number is
    422 and suggest_assignee is not called."""
    mocked = _patch(monkeypatch)
    with logged_in_as(ADMIN):
        resp = await client.post(PATH, json={field: value})
    assert resp.status_code == 422
    mocked.assert_not_awaited()


@pytest.mark.parametrize(
    "payload", [{}, {"name": None, "company": None, "industry": None, "address": None}]
)
async def test_empty_body_calls_service_with_none(
    client, async_session_maker, monkeypatch, payload
):
    """Acceptance 1 (all four fields optional): nothing given or all null still
    calls suggest_assignee, with the four fields as None."""
    mocked = _patch(monkeypatch)
    with logged_in_as(ADMIN):
        resp = await client.post(PATH, json=payload)
    assert resp.status_code == 200
    mocked.assert_awaited_once()
    assert mocked.await_args.kwargs == {
        "name": None,
        "company": None,
        "industry": None,
        "address": None,
    }


async def test_not_logged_in_is_401(client, async_session_maker, monkeypatch):
    """Acceptance 2 (login required): an invalid token is 401 and suggest_assignee
    is not called."""
    mocked = _patch(monkeypatch)
    resp = await client.post(PATH, json={}, headers={"Authorization": "Bearer not-a-valid-token"})
    assert resp.status_code == 401
    mocked.assert_not_awaited()


async def test_sales_is_403_without_calling_service(client, async_session_maker, monkeypatch):
    """Acceptance 2 (roles other than admin and manager get 403 FORBIDDEN in the
    fail envelope, without the business function)."""
    mocked = _patch(monkeypatch)
    with logged_in_as(REP):
        resp = await client.post(PATH, json={"name": "New lead"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FORBIDDEN"
    mocked.assert_not_awaited()


@pytest.mark.parametrize("user", [BOSS, ADMIN])
async def test_manager_and_admin_are_200(client, async_session_maker, monkeypatch, user):
    """Acceptance 2 (admin and manager get 200 from suggest_assignee)."""
    mocked = _patch(monkeypatch)
    with logged_in_as(user):
        resp = await client.post(PATH, json={"name": "New lead"})
    assert resp.status_code == 200
    mocked.assert_awaited_once()


async def test_service_gets_user_and_fields(client, async_session_maker, monkeypatch):
    """Acceptance 2 (suggest_assignee gets the current user and the request's four
    fields as given)."""
    mocked = _patch(monkeypatch)
    payload = {
        "name": "Ali",
        "company": "Roof Co",
        "industry": "Construction",
        "address": "Jalan 1, Kuala Lumpur",
    }
    with logged_in_as(BOSS):
        resp = await client.post(PATH, json=payload)
    assert resp.status_code == 200
    args = mocked.await_args
    assert args.args[1] is BOSS
    assert args.kwargs == payload


async def test_data_is_service_result(client, async_session_maker, monkeypatch):
    """Acceptance 2 (200 wraps rule_pick and candidates in the ok envelope, as
    the business function gives them)."""
    _patch(monkeypatch)
    with logged_in_as(ADMIN):
        resp = await client.post(PATH, json={"industry": "Construction"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == RESULT


def test_path_is_not_taken_by_contact_id_routes():
    """Acceptance 1 (no clash with the /{contact_id} routes): the first route
    matching POST on this path is the assign-suggestion endpoint."""
    scope = {"type": "http", "path": PATH, "method": "POST"}
    first = next(route for route in app.router.routes if route.matches(scope)[0] == Match.FULL)
    assert first.endpoint is contacts.suggest_assignee
    assert "{contact_id}" not in first.path
