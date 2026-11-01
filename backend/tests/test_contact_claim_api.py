"""CRM-TASK-094: claim and release endpoints, 409 CLAIMED on send and retry, and the
claim fields of the contact list, contact detail and conversation list.

AC1 = POST claim, AC2 = DELETE claim, AC3 = 409 CLAIMED (also on send and retry),
AC4 = claim fields in the lists and detail. Claims are judged by the server clock,
so seeded claims are placed relative to datetime.utcnow().
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select

from app.config import settings
from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_send_service, contact_service

ADMIN = User(id="admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")
REP = User(id="rep", name="Rep", email="rep@example.com", password_hash="x", role="sales")
OTHER = User(id="other", name="Other", email="other@example.com", password_hash="x", role="sales")


def _at(minutes):
    """Server time plus ``minutes``, whole seconds so the output is predictable."""
    return datetime.utcnow().replace(microsecond=0) + timedelta(minutes=minutes)


def _utc(value):
    return value.isoformat() + "Z"


def _users():
    return [
        User(id=u.id, name=u.name, email=u.email, password_hash="x", role=u.role)
        for u in (ADMIN, REP, OTHER)
    ]


def _contact(contact_id="contact", **kwargs):
    values = {"id": contact_id, "name": contact_id.title(), "phone": "60123456789"}
    values.update({"assigned_to": "rep"}, **kwargs)
    return Contact(**values)


def _inbound(message_id="inbound", contact_id="contact", created_at=None):
    return Message(
        id=message_id,
        contact_id=contact_id,
        channel="whatsapp",
        direction="inbound",
        sender_id="visitor",
        recipient_id="crm",
        body="hello",
        assigned_to="rep",
        created_at=created_at or _at(-60),
    )


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all([*_users(), *rows])
        await session.commit()


async def _stored_claim(maker, contact_id="contact"):
    async with maker() as session:
        contact = await session.get(Contact, contact_id)
        return contact.reply_claimed_by, contact.reply_claimed_until


async def _request(client, user, method, path, **kwargs):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        return await client.request(method, path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _until(response, key="reply_claimed_until"):
    return datetime.fromisoformat(response.json()["data"][key].removesuffix("Z"))


# --- POST /api/contacts/{id}/claim ---


async def test_claim_without_a_claim_takes_it_for_fifteen_minutes(client, async_session_maker):
    """Guards AC1 (success is 200 with the five fields, nobody taken over; the claim is
    stored for now + 15 minutes)."""
    await _seed(async_session_maker, _contact())
    before = datetime.utcnow()
    response = await _request(client, REP, "POST", "/api/contacts/contact/claim")
    after = datetime.utcnow()

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reply_claimed_by"] == "rep"
    assert data["reply_claimed_by_name"] == "Rep"
    assert data["taken_over_from"] is None
    assert data["taken_over_from_name"] is None
    assert data["reply_claimed_until"].endswith("Z")
    until = _until(response)
    assert before + timedelta(minutes=15) <= until <= after + timedelta(minutes=15)
    assert await _stored_claim(async_session_maker) == ("rep", until)


async def test_claim_renews_own_claim(client, async_session_maker):
    """Guards AC1 (one's own valid claim is renewed, and nobody is reported taken over,
    with or without takeover)."""
    old_until = _at(2)
    await _seed(
        async_session_maker, _contact(reply_claimed_by="rep", reply_claimed_until=old_until)
    )
    for body in (None, {"takeover": True}):
        response = await _request(client, REP, "POST", "/api/contacts/contact/claim", json=body)
        assert response.status_code == 200
        assert response.json()["data"]["taken_over_from"] is None
        assert response.json()["data"]["taken_over_from_name"] is None
        assert _until(response) > old_until + timedelta(minutes=10)
        assert await _stored_claim(async_session_maker) == ("rep", _until(response))


@pytest.mark.parametrize(
    ("holder", "holder_name"), [("admin", "Admin"), ("gone", None)], ids=["user", "user-gone"]
)
async def test_claim_held_by_another_user_is_409_claimed(
    client, async_session_maker, holder, holder_name
):
    """Guards AC1 and AC3 (another user's valid claim without takeover: 409 CLAIMED in the
    fail envelope, fields carry holder, name -- empty once the user is gone -- and
    expiry; the claim is unchanged)."""
    until = _at(10)
    await _seed(async_session_maker, _contact(reply_claimed_by=holder, reply_claimed_until=until))
    response = await _request(
        client, REP, "POST", "/api/contacts/contact/claim", json={"takeover": False}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "CLAIMED"
    assert body["error"]["fields"] == {
        "claimed_by": holder,
        "claimed_by_name": holder_name,
        "claimed_until": _utc(until),
    }
    assert await _stored_claim(async_session_maker) == (holder, until)


async def test_takeover_returns_who_was_taken_over(client, async_session_maker):
    """Guards AC1 (takeover overwrites another user's valid claim and returns that user's
    id and name)."""
    await _seed(async_session_maker, _contact(reply_claimed_by="rep", reply_claimed_until=_at(10)))
    response = await _request(
        client, ADMIN, "POST", "/api/contacts/contact/claim", json={"takeover": True}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["reply_claimed_by"], data["reply_claimed_by_name"]) == ("admin", "Admin")
    assert (data["taken_over_from"], data["taken_over_from_name"]) == ("rep", "Rep")
    assert await _stored_claim(async_session_maker) == ("admin", _until(response))


async def test_takeover_of_an_expired_claim_reports_nobody(client, async_session_maker):
    """Guards AC1 (taken_over_from is empty when there is no valid claim)."""
    await _seed(async_session_maker, _contact(reply_claimed_by="rep", reply_claimed_until=_at(-1)))
    response = await _request(
        client, ADMIN, "POST", "/api/contacts/contact/claim", json={"takeover": True}
    )
    assert response.status_code == 200
    assert response.json()["data"]["taken_over_from"] is None
    assert response.json()["data"]["taken_over_from_name"] is None


@pytest.mark.parametrize("takeover", ["true", 1, 0, None, [], {}], ids=repr)
async def test_takeover_of_the_wrong_type_is_422(client, async_session_maker, takeover):
    """Guards AC1 (takeover must be a JSON boolean; anything else is 422 and claims
    nothing)."""
    await _seed(async_session_maker, _contact())
    response = await _request(
        client, REP, "POST", "/api/contacts/contact/claim", json={"takeover": takeover}
    )
    assert response.status_code == 422
    assert await _stored_claim(async_session_maker) == (None, None)


# --- 403 shared by POST and DELETE ---


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_no_access_missing_and_deleted_share_one_403(client, async_session_maker, method):
    """Guards AC1 and AC2 (not accessible, missing, and deleted -- even for an admin --
    all return the same 403 NOT_ASSIGNED and change nothing)."""
    await _seed(
        async_session_maker,
        _contact(),
        _contact("deleted", deleted_at=datetime.utcnow()),
    )
    no_access = await _request(client, OTHER, method, "/api/contacts/contact/claim")
    missing = await _request(client, REP, method, "/api/contacts/missing/claim")
    missing_admin = await _request(client, ADMIN, method, "/api/contacts/missing/claim")
    deleted_admin = await _request(client, ADMIN, method, "/api/contacts/deleted/claim")

    responses = (no_access, missing, missing_admin, deleted_admin)
    assert all(r.status_code == 403 for r in responses)
    assert no_access.json()["error"]["code"] == "NOT_ASSIGNED"
    assert all(r.json() == no_access.json() for r in responses)
    assert await _stored_claim(async_session_maker) == (None, None)
    assert await _stored_claim(async_session_maker, "deleted") == (None, None)


# --- DELETE /api/contacts/{id}/claim ---

_CLEARED = {"reply_claimed_by": None, "reply_claimed_by_name": None, "reply_claimed_until": None}


@pytest.mark.parametrize(
    ("holder", "minutes"),
    [(None, None), ("rep", 10), ("admin", -1)],
    ids=["no-claim", "own-claim", "expired-claim"],
)
async def test_release_without_another_valid_claim_clears(
    client, async_session_maker, holder, minutes
):
    """Guards AC2 (no valid claim or one's own: 200 and both columns cleared)."""
    until = _at(minutes) if minutes is not None else None
    await _seed(async_session_maker, _contact(reply_claimed_by=holder, reply_claimed_until=until))
    response = await _request(client, REP, "DELETE", "/api/contacts/contact/claim")
    assert response.status_code == 200
    assert response.json()["data"] == _CLEARED
    assert await _stored_claim(async_session_maker) == (None, None)


async def test_release_of_another_users_claim_without_takeover_is_409(client, async_session_maker):
    """Guards AC2 and AC3 (another user's valid claim without takeover: 409 CLAIMED with
    its fields; the claim stays)."""
    until = _at(10)
    await _seed(async_session_maker, _contact(reply_claimed_by="admin", reply_claimed_until=until))
    for path in ("/api/contacts/contact/claim", "/api/contacts/contact/claim?takeover=false"):
        response = await _request(client, REP, "DELETE", path)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CLAIMED"
        assert response.json()["error"]["fields"] == {
            "claimed_by": "admin",
            "claimed_by_name": "Admin",
            "claimed_until": _utc(until),
        }
    assert await _stored_claim(async_session_maker) == ("admin", until)


async def test_release_of_another_users_claim_with_takeover_clears(client, async_session_maker):
    """Guards AC2 (takeover=true clears another user's valid claim)."""
    await _seed(
        async_session_maker, _contact(reply_claimed_by="admin", reply_claimed_until=_at(10))
    )
    response = await _request(client, REP, "DELETE", "/api/contacts/contact/claim?takeover=true")
    assert response.status_code == 200
    assert response.json()["data"] == _CLEARED
    assert await _stored_claim(async_session_maker) == (None, None)


# --- Send and retry ---


def _configured(monkeypatch):
    monkeypatch.setattr(settings, "chatbot_send_url", "http://chatbot.internal:8000")
    monkeypatch.setattr(settings, "chatbot_send_secret", "secret")


async def _outbound(maker):
    async with maker() as session:
        result = await session.execute(select(Message).where(Message.direction == "outbound"))
        return result.scalars().all()


async def test_send_while_another_user_holds_the_claim_is_409(
    client, async_session_maker, monkeypatch
):
    """Guards AC3 (POST /api/messages/whatsapp/send meeting another user's claim: 409
    CLAIMED with its fields, no message stored, chatbot not called)."""
    until = _at(10)
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by="admin", reply_claimed_until=until),
        _inbound(),
    )
    _configured(monkeypatch)
    post = AsyncMock()
    monkeypatch.setattr(chatbot_send_service, "_post_chatbot", post)

    response = await _request(
        client,
        REP,
        "POST",
        "/api/messages/whatsapp/send",
        json={"contact_id": "contact", "message": "hi"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLAIMED"
    assert response.json()["error"]["fields"] == {
        "claimed_by": "admin",
        "claimed_by_name": "Admin",
        "claimed_until": _utc(until),
    }
    assert await _outbound(async_session_maker) == []
    post.assert_not_awaited()
    assert await _stored_claim(async_session_maker) == ("admin", until)


async def test_send_claim_comes_after_403_and_before_no_phone(client, async_session_maker):
    """Guards AC3 (CLAIMED sits after 403 and before NO_PHONE)."""
    await _seed(
        async_session_maker,
        _contact(phone=None, reply_claimed_by="admin", reply_claimed_until=_at(10)),
    )
    body = {"contact_id": "contact", "message": "hi"}
    claimed = await _request(client, REP, "POST", "/api/messages/whatsapp/send", json=body)
    hidden = await _request(client, OTHER, "POST", "/api/messages/whatsapp/send", json=body)
    assert claimed.json()["error"]["code"] == "CLAIMED"
    assert (hidden.status_code, hidden.json()["error"]["code"]) == (403, "NOT_ASSIGNED")


def _agent_message():
    return Message(
        id="agent",
        contact_id="contact",
        channel="whatsapp",
        direction="outbound",
        sender_id="ai_chatbot_demo",
        recipient_id="60123456789",
        body="original reply",
        is_read=True,
        assigned_to="rep",
        sent_by="rep",
        author_kind="agent",
        created_at=_at(-30),
    )


async def test_retry_while_another_user_holds_the_claim_is_409(
    client, async_session_maker, monkeypatch
):
    """Guards AC3 (POST /api/messages/{id}/whatsapp/retry meeting another user's claim:
    409 CLAIMED with its fields, chatbot not called, message unchanged)."""
    until = _at(10)
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by="admin", reply_claimed_until=until),
        _agent_message(),
        _inbound(),
    )
    _configured(monkeypatch)
    post = AsyncMock()
    monkeypatch.setattr(chatbot_send_service, "_post_chatbot", post)

    response = await _request(client, REP, "POST", "/api/messages/agent/whatsapp/retry")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLAIMED"
    assert response.json()["error"]["fields"] == {
        "claimed_by": "admin",
        "claimed_by_name": "Admin",
        "claimed_until": _utc(until),
    }
    post.assert_not_awaited()
    outbound = await _outbound(async_session_maker)
    assert [(m.id, m.external_id, m.delivery_status) for m in outbound] == [("agent", None, None)]
    assert await _stored_claim(async_session_maker) == ("admin", until)


async def test_retry_passes_the_requester_to_the_retry_service(
    client, async_session_maker, monkeypatch
):
    """Guards AC3 (the retry endpoint hands the requesting user to the retry service)."""
    await _seed(async_session_maker, _contact(), _agent_message())
    retry = AsyncMock(
        return_value=chatbot_send_service.SendReplyResult(
            chatbot_send_service.UNCONFIRMED, message=_agent_message()
        )
    )
    monkeypatch.setattr(chatbot_send_service, "retry_agent_reply", retry)
    response = await _request(client, ADMIN, "POST", "/api/messages/agent/whatsapp/retry")
    assert response.status_code == 202
    assert retry.await_args.kwargs["requester_id"] == "admin"


# --- Claim fields of the lists and detail ---


async def _seed_three_claims(maker, *extra):
    """valid: Rep until +10 minutes; expired: Rep until -1 minute; none."""
    until = _at(10)
    await _seed(
        maker,
        _contact("valid", reply_claimed_by="rep", reply_claimed_until=until),
        _contact("expired", reply_claimed_by="rep", reply_claimed_until=_at(-1)),
        _contact("none"),
        *extra,
    )
    return {
        "valid": {
            "reply_claimed_by": "rep",
            "reply_claimed_by_name": "Rep",
            "reply_claimed_until": _utc(until),
        },
        "expired": _CLEARED,
        "none": _CLEARED,
    }


def _claim_of(item):
    return {key: item[key] for key in _CLEARED}


async def test_contact_list_and_detail_carry_the_claim(client, async_session_maker):
    """Guards AC4 (contact list and detail: valid claim with UTC-marked expiry; expired
    or no claim reads as three empty fields)."""
    expected = await _seed_three_claims(async_session_maker)

    listing = await _request(client, ADMIN, "GET", "/api/contacts")
    listed = {item["id"]: _claim_of(item) for item in listing.json()["data"]["data"]}
    assert listed == expected

    for contact_id, claim in expected.items():
        detail = await _request(client, ADMIN, "GET", f"/api/contacts/{contact_id}")
        assert _claim_of(detail.json()["data"]) == claim


async def test_conversation_list_carries_the_contacts_claim(client, async_session_maker):
    """Guards AC4 (each conversation carries its contact's claim; a conversation with no
    contact has three empty fields)."""
    stranger = Message(
        id="stranger",
        contact_id=None,
        channel="whatsapp",
        direction="inbound",
        sender_id="60999999999",
        recipient_id="crm",
        body="hi",
        assigned_to=None,
        created_at=_at(-60),
    )
    expected = await _seed_three_claims(
        async_session_maker,
        _inbound("m-valid", "valid"),
        _inbound("m-expired", "expired"),
        _inbound("m-none", "none"),
        stranger,
    )
    expected = {f"m-{contact_id}": claim for contact_id, claim in expected.items()}
    expected["stranger"] = _CLEARED

    response = await _request(client, ADMIN, "GET", "/api/messages/conversations")
    conversations = {item["id"]: _claim_of(item) for item in response.json()["data"]["data"]}
    assert conversations == expected


async def test_claim_fields_are_looked_up_once_per_page(client, async_session_maker, monkeypatch):
    """Guards AC4 (the new claim fields cost one batched name query per page, and the
    conversation list reads its contacts' claims in one query)."""
    await _seed(
        async_session_maker,
        _contact("a", reply_claimed_by="rep", reply_claimed_until=_at(10)),
        _contact("b", reply_claimed_by="admin", reply_claimed_until=_at(10)),
        _contact("c", reply_claimed_by="other", reply_claimed_until=_at(10)),
        _inbound("m-a", "a"),
        _inbound("m-b", "b"),
        _inbound("m-c", "c"),
    )
    names = AsyncMock(wraps=contact_service._user_names)
    monkeypatch.setattr(contact_service, "_user_names", names)
    claim_reads = []
    engine = async_session_maker.kw["bind"].sync_engine

    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "reply_claimed_until" in statement:
            claim_reads.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        listing = await _request(client, ADMIN, "GET", "/api/contacts")
        assert len(listing.json()["data"]["data"]) == 3
        assert names.await_count == 1

        names.reset_mock()
        claim_reads.clear()
        conversations = await _request(client, ADMIN, "GET", "/api/messages/conversations")
        assert len(conversations.json()["data"]["data"]) == 3
        assert names.await_count == 1
        assert len(claim_reads) == 1
    finally:
        event.remove(engine, "before_cursor_execute", count)
