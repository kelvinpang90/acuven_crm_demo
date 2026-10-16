"""CRM-TASK-037 API mappings for deal AI next-step suggestions."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.user import User
from app.services import ai_assist_service, deal_next_step_service

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="other", name="Other", email="other@example.com", password_hash="x", role="sales")
PATH = "/api/ai/deals/deal/next-step"


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
                Deal(id="deal", contact_id="contact", title="Deal", assigned_to="rep"),
            ]
        )
        await session.commit()


async def _request(client, user, path=PATH, **kwargs):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.post(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_deal_next_step_requires_login(client, async_session_maker):
    """Guards the acceptance criterion that the endpoint requires login."""
    await _seed(async_session_maker)
    response = await client.post(PATH, json={})
    assert response.status_code in {401, 403}


async def test_deal_next_step_rejects_invalid_language_without_business_call(
    client, async_session_maker, monkeypatch
):
    """Guards the acceptance criterion that unsupported output_language returns 422 without a business call."""
    await _seed(async_session_maker)
    mocked = AsyncMock()
    monkeypatch.setattr(deal_next_step_service, "generate_deal_next_step", mocked)
    response = await _request(client, REP, json={"output_language": "fr"})
    assert response.status_code == 422
    mocked.assert_not_awaited()


@pytest.mark.parametrize("language,expected", [("zh", "zh-CN"), ("en", "en")])
async def test_deal_next_step_defaults_to_current_user_interface_language(
    client, async_session_maker, monkeypatch, language, expected
):
    """Guards the acceptance criterion that omitted output_language follows the current user's interface language."""
    await _seed(async_session_maker)
    REP.language = language
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(deal_next_step_service, "generate_deal_next_step", mocked)
    response = await _request(client, REP, json={})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == expected


async def test_deal_next_step_preserves_explicit_output_language(
    client, async_session_maker, monkeypatch
):
    """Guards the acceptance criterion that an explicit valid output_language is passed through unchanged."""
    await _seed(async_session_maker)
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(deal_next_step_service, "generate_deal_next_step", mocked)
    response = await _request(client, REP, json={"output_language": "ms"})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["output_language"] == "ms"


@pytest.mark.parametrize(
    "user,path,deleted_target,contact_owner",
    [
        (OTHER, PATH, None, None),
        (REP, PATH, None, "other"),
        (ADMIN, "/api/ai/deals/missing/next-step", None, None),
        (REP, PATH, "deal", None),
        (REP, PATH, "contact", None),
    ],
)
async def test_deal_next_step_hides_inaccessible_missing_and_deleted_records_without_business_call(
    client, async_session_maker, monkeypatch, user, path, deleted_target, contact_owner
):
    """Guards the acceptance criterion that inaccessible, missing, and deleted deals or contacts return one 403 without a business call."""
    await _seed(async_session_maker)
    if deleted_target or contact_owner:
        async with async_session_maker() as session:
            if deleted_target == "deal":
                record = await session.get(Deal, "deal")
            else:
                record = await session.get(Contact, "contact")
            if deleted_target:
                record.deleted_at = datetime.utcnow()
            if contact_owner:
                record.assigned_to = contact_owner
            await session.commit()
    mocked = AsyncMock()
    monkeypatch.setattr(deal_next_step_service, "generate_deal_next_step", mocked)
    response = await _request(client, user, path, json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "NOT_ASSIGNED"
    mocked.assert_not_awaited()


async def test_deal_next_step_passes_current_user_and_deal_id_to_business_logic(
    client, async_session_maker, monkeypatch
):
    """Guards the acceptance criterion that an assigned sales user reaches business logic with the requested deal and current user."""
    await _seed(async_session_maker)
    mocked = AsyncMock(return_value=ai_assist_service.AiAssistResult(ai_assist_service.OK))
    monkeypatch.setattr(deal_next_step_service, "generate_deal_next_step", mocked)
    response = await _request(client, REP, json={})
    assert response.status_code == 200
    assert mocked.await_args.kwargs["user"] is REP
    assert mocked.await_args.kwargs["deal_id"] == "deal"


@pytest.mark.parametrize(
    "category,status,code",
    [
        (ai_assist_service.DEAL_CLOSED, 400, "DEAL_CLOSED"),
        (ai_assist_service.NO_HISTORY, 400, "NO_HISTORY"),
        (ai_assist_service.NOT_CONFIGURED, 503, "AI_NOT_CONFIGURED"),
        (ai_assist_service.UNAVAILABLE, 503, "AI_UNAVAILABLE"),
        (ai_assist_service.ERROR, 502, "AI_ERROR"),
        (ai_assist_service.INVALID_INPUT, 422, "INVALID_INPUT"),
    ],
)
async def test_deal_next_step_maps_every_business_failure(
    client, async_session_maker, monkeypatch, category, status, code
):
    """Guards the acceptance criterion that every specified business result uses its HTTP error mapping."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        deal_next_step_service,
        "generate_deal_next_step",
        AsyncMock(return_value=ai_assist_service.AiAssistResult(category)),
    )
    response = await _request(client, REP, json={})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_deal_next_step_success_returns_the_eight_field_contract(
    client, async_session_maker, monkeypatch
):
    """Guards the acceptance criterion that successful next-step suggestions return all eight response fields."""
    await _seed(async_session_maker)
    monkeypatch.setattr(
        deal_next_step_service,
        "generate_deal_next_step",
        AsyncMock(
            return_value=ai_assist_service.AiAssistResult(
                ai_assist_service.OK,
                request_id="next-step-id",
                assessment="Assessment",
                stall_reason="Waiting",
                next_steps=["Follow up"],
                idle_days=4,
                days_in_status=8,
                stalled=True,
                truncated=False,
            )
        ),
    )
    response = await _request(client, REP, json={})
    assert response.status_code == 200
    assert response.json()["data"] == {
        "request_id": "next-step-id",
        "assessment": "Assessment",
        "stall_reason": "Waiting",
        "next_steps": ["Follow up"],
        "idle_days": 4,
        "days_in_status": 8,
        "stalled": True,
        "truncated": False,
    }
