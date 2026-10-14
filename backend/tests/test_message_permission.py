"""Only whoever the customer belongs to may write to them.

A shared WhatsApp number means several reps work one inbox, so "can I see it"
and "may I answer it" have to be the same question. Both send endpoints took a
current_user and never looked at it.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.user import User
from app.services.access_service import may_access_contact

REP = User(id="u-rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="u-other", name="Other", email="other@example.com", password_hash="x", role="sales")
BOSS = User(id="u-boss", name="Boss", email="boss@example.com", password_hash="x", role="manager")
ADMIN = User(id="u-admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")


async def _seed(session_maker) -> None:
    async with session_maker() as session:
        session.add_all(
            [
                User(
                    id="u-rep",
                    name="Rep",
                    email="rep@example.com",
                    password_hash="x",
                    role="sales",
                    manager_id="u-boss",
                ),
                User(
                    id="u-other",
                    name="Other",
                    email="other@example.com",
                    password_hash="x",
                    role="sales",
                ),
                User(
                    id="u-boss",
                    name="Boss",
                    email="boss@example.com",
                    password_hash="x",
                    role="manager",
                ),
                Contact(id="c-mine", name="Mine", phone="60111111111", assigned_to="u-rep"),
                Contact(id="c-theirs", name="Theirs", phone="60122222222", assigned_to="u-other"),
                Contact(id="c-orphan", name="Orphan", phone="60133333333", assigned_to=None),
            ]
        )
        await session.commit()


@pytest.mark.parametrize(
    "user, contact_id, allowed",
    [
        (REP, "c-mine", True),
        (REP, "c-theirs", False),
        (REP, "c-orphan", False),
        (REP, "c-missing", False),
        (BOSS, "c-mine", True),
        (BOSS, "c-theirs", False),
        (BOSS, "c-orphan", False),
        (ADMIN, "c-theirs", True),
        (ADMIN, "c-orphan", True),
    ],
)
async def test_permission_matrix(async_session_maker, user, contact_id, allowed):
    await _seed(async_session_maker)
    async with async_session_maker() as session:
        assert await may_access_contact(session, user, contact_id) is allowed


async def test_send_endpoint_refuses_and_sends_nothing(client, async_session_maker):
    """The refusal has to happen before the provider call, not after."""
    await _seed(async_session_maker)

    app.dependency_overrides[get_current_user] = lambda: REP
    try:
        with patch(
            "app.services.chatbot_send_service.send_agent_reply", new=AsyncMock()
        ) as mock_send:
            resp = await client.post(
                "/api/messages/whatsapp/send",
                json={"contact_id": "c-theirs", "message": "hello"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "NOT_ASSIGNED"
    mock_send.assert_not_awaited()
