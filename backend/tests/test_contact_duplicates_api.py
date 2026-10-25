"""The duplicate-check and per-customer duplicates routes
(docs/contact-duplicate-hints.md 6.2 and 9.1; CRM-TASK-061)."""

from datetime import datetime

import pytest

from app.models.contact import Contact
from app.services import duplicate_service
from tests._people import ADMIN, REP, logged_in_as, seed_people


async def _add(session_maker, *contacts: Contact) -> None:
    async with session_maker() as session:
        session.add_all(contacts)
        await session.commit()


async def test_duplicate_check_requires_login(client, async_session_maker):
    """Acceptance 1 (duplicate-check requires login): no user, no answer."""
    resp = await client.post("/api/contacts/duplicate-check", json={"phone": "60111111111"})
    assert resp.status_code in (401, 403)


@pytest.mark.parametrize(
    "field, length",
    [("name", 101), ("company", 201), ("email", 201), ("phone", 31)],
)
async def test_duplicate_check_rejects_overlong_fields(client, async_session_maker, field, length):
    """Acceptance 1 (duplicate-check validates each field against the contacts
    column length): one character over is 422."""
    with logged_in_as(REP):
        resp = await client.post("/api/contacts/duplicate-check", json={field: "x" * length})
    assert resp.status_code == 422


async def test_duplicate_check_all_empty_returns_empty_without_query(
    client, async_session_maker, monkeypatch
):
    """Acceptance 1 (four empty fields return an empty result without querying)."""
    await seed_people(async_session_maker)

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("find_duplicates must not run for an empty check")

    monkeypatch.setattr(duplicate_service, "find_duplicates", _must_not_run)
    with logged_in_as(REP):
        resp = await client.post(
            "/api/contacts/duplicate-check",
            json={"name": None, "company": "", "email": "  ", "phone": None},
        )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"visible": [], "hidden_count": 0}


async def test_duplicate_check_hides_someone_elses_customer(client, async_session_maker):
    """Acceptance 4 item 2 (a rep only gets a count for another rep's customer,
    never its name), via acceptance 1's visible and hidden_count."""
    await seed_people(async_session_maker)
    with logged_in_as(REP):
        resp = await client.post(
            "/api/contacts/duplicate-check", json={"name": "New lead", "phone": "60122222222"}
        )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"visible": [], "hidden_count": 1}
    assert "Theirs" not in resp.text


async def test_duplicates_of_someone_elses_customer_is_not_found(client, async_session_maker):
    """Acceptance 2 (a customer the user may not access answers exactly like
    GET /api/contacts/{contact_id}: 404 NOT_FOUND)."""
    await seed_people(async_session_maker)
    with logged_in_as(REP):
        duplicates = await client.get("/api/contacts/c-theirs/duplicates")
        detail = await client.get("/api/contacts/c-theirs")
    assert duplicates.status_code == detail.status_code == 404
    assert duplicates.json() == detail.json()
    assert duplicates.json()["error"]["code"] == "NOT_FOUND"


async def test_duplicates_of_own_customer_leave_itself_out(client, async_session_maker):
    """Acceptance 2 (the customer's own details with exclude_id): a match is
    listed and the customer itself is not."""
    await seed_people(async_session_maker)
    await _add(
        async_session_maker,
        Contact(id="c-mine-2", name="Mine again", phone="60111111111", assigned_to="u-rep"),
    )
    with logged_in_as(REP):
        resp = await client.get("/api/contacts/c-mine/duplicates")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [item["id"] for item in data["visible"]] == ["c-mine-2"]
    assert data["visible"][0]["rules"] == ["phone"]
    assert data["hidden_count"] == 0


@pytest.mark.parametrize("user", [REP, ADMIN])
async def test_duplicates_of_deleted_customer_is_not_found(client, async_session_maker, user):
    """Acceptance 2 (a deleted customer is 404 NOT_FOUND), for the owner and for
    an admin, whom may_access_contact lets through for any id."""
    await seed_people(async_session_maker)
    await _add(
        async_session_maker,
        Contact(
            id="c-gone",
            name="Gone",
            phone="60111111111",
            assigned_to="u-rep",
            deleted_at=datetime(2026, 10, 1),
        ),
    )
    with logged_in_as(user):
        resp = await client.get("/api/contacts/c-gone/duplicates")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_get_contact_unchanged(client, async_session_maker):
    """Acceptance 3 (existing contact routes keep their behaviour): GET by id."""
    await seed_people(async_session_maker)
    with logged_in_as(REP):
        resp = await client.get("/api/contacts/c-mine")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "c-mine"
    assert resp.json()["data"]["name"] == "Mine"


async def test_put_contact_unchanged(client, async_session_maker):
    """Acceptance 3 (existing contact routes keep their behaviour): PUT by id
    still answers with the updated contact. The test get_db does not commit,
    so the response is what this asserts on, not a re-read of the row."""
    await seed_people(async_session_maker)
    with logged_in_as(REP):
        resp = await client.put("/api/contacts/c-mine", json={"name": "Renamed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == "c-mine"
    assert body["data"]["name"] == "Renamed"
