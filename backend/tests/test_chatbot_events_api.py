"""POST /internal/chatbot/events: signature, body validation and response shape
(docs/chatbot-events.md section 4 and 9.5).

Each test names the acceptance criterion of CRM-TASK-008 it guards:
AC1 the endpoint and its signature check, AC2 body validation, AC3 handing the
event over and the response shape. Signatures are recomputed here from the
contract, not with the endpoint's own helper.
"""

import hashlib
import hmac
import json
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models.contact import Contact
from app.models.message import Message
from app.routers import internal_chatbot
from app.services import chatbot_event_service

URL = "/internal/chatbot/events"
SECRET = "test-only-current-key"
OLD_SECRET = "test-only-previous-key"
NOW = 1_760_256_000
PHONE = "60123456789"
OTHER_PHONE = "60198765432"
BSUID = "MY.visitor-1"
EVENT_ID = "6f1c2b9e-1111-4222-8333-944455556666"
BSUID_ONLY = {"phone": None, "bsuid": BSUID}
TS_HEADER = "X-Acuven-Timestamp"
SIG_HEADER = "X-Acuven-Signature"

_DROP = object()


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Current key set, no previous key, and a fixed clock."""
    monkeypatch.setattr(settings, "chatbot_events_secret", SECRET)
    monkeypatch.setattr(settings, "chatbot_events_secret_previous", "")
    monkeypatch.setattr(internal_chatbot, "_now", lambda: NOW)


def _sign(raw: bytes, secret: str = SECRET, ts: int = NOW) -> dict:
    """The contract's signature: HMAC-SHA256(secret UTF-8, b"<ts>." + raw body)."""
    signed = str(ts).encode("ascii") + b"." + raw
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return {"X-Acuven-Timestamp": str(ts), "X-Acuven-Signature": f"sha256={digest}"}


def _encode(body: dict) -> bytes:
    return json.dumps(body).encode("utf-8")


async def _post(client, body, headers=None):
    raw = body if isinstance(body, bytes) else _encode(body)
    if headers is None:
        headers = _sign(raw)
    headers = {"Content-Type": "application/json", **headers}
    return await client.post(URL, content=raw, headers=headers)


def _valid(event_type: str, **changes) -> dict:
    """A valid body of the given type; `changes` replace top-level keys (_DROP removes one)."""
    body = {
        "event_id": EVENT_ID,
        "type": event_type,
        "occurred_at": "2026-10-12T08:00:00Z",
        "visitor": {"phone": PHONE, "bsuid": None, "profile_name": "Ann"},
    }
    if event_type == "customer_message":
        body["message"] = {"wa_message_id": "wamid.in", "body": "hello"}
    elif event_type == "ai_reply":
        body["message"] = {"wa_message_id": "wamid.ai", "body": "Hi, how can I help?"}
        body["bot_id"] = "bot-dental"
    elif event_type == "console_reply":
        body["message"] = {"wa_message_id": "wamid.console", "body": "A person here"}
    elif event_type == "handover_changed":
        body["handover"] = {"active": True, "expires_at": "2026-10-12T08:30:00Z"}
    elif event_type == "delivery_status":
        body["status"] = {
            "wa_message_id": "wamid.out",
            "status": "delivered",
            "error_code": None,
            "error_title": None,
        }
    for key, value in changes.items():
        if value is _DROP:
            body.pop(key, None)
        else:
            body[key] = value
    return body


async def _seed(maker) -> None:
    """Contact c1 with PHONE and one message the chatbot sent it."""
    async with maker() as session:
        session.add_all(
            [
                Contact(id="c1", name="Ann", phone=PHONE),
                Message(
                    id="m-out",
                    contact_id="c1",
                    channel="whatsapp",
                    direction="outbound",
                    sender_id="ai_chatbot_demo",
                    recipient_id=PHONE,
                    body="earlier reply",
                    external_id="wamid.out",
                    is_read=True,
                ),
            ]
        )
        await session.commit()


async def _messages(maker) -> dict:
    async with maker() as session:
        rows = (await session.execute(select(Message))).scalars().all()
    return {m.external_id: m for m in rows}


async def _contact(maker, contact_id: str = "c1") -> Contact:
    async with maker() as session:
        query = select(Contact).where(Contact.id == contact_id)
        return (await session.execute(query)).scalar_one()


async def _unchanged(maker) -> None:
    """Nothing beyond the seed was written."""
    assert set(await _messages(maker)) == {"wamid.out"}
    message = (await _messages(maker))["wamid.out"]
    assert message.delivery_status is None
    contact = await _contact(maker)
    assert contact.wa_handover_active is False
    async with maker() as session:
        assert len((await session.execute(select(Contact))).scalars().all()) == 1


# --- AC1 + AC3: each of the five events, correctly signed, is handled ---


async def test_customer_message_is_stored(client, async_session_maker):
    """AC1 + AC3: signed customer_message -> 200 {"result": "stored"}, unread inbound message."""
    await _seed(async_session_maker)
    resp = await _post(client, _valid("customer_message"))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}

    message = (await _messages(async_session_maker))["wamid.in"]
    assert message.contact_id == "c1"
    assert message.author_kind == "customer"
    assert message.direction == "inbound"
    assert message.body == "hello"
    assert message.is_read is False


async def test_ai_reply_is_stored(client, async_session_maker):
    """AC1 + AC3: signed ai_reply -> stored, author ai, bot_id kept."""
    await _seed(async_session_maker)
    resp = await _post(client, _valid("ai_reply"))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}

    message = (await _messages(async_session_maker))["wamid.ai"]
    assert message.author_kind == "ai"
    assert message.bot_id == "bot-dental"
    assert message.direction == "outbound"


async def test_console_reply_is_stored(client, async_session_maker):
    """AC1 + AC3: signed console_reply -> stored, author console."""
    await _seed(async_session_maker)
    resp = await _post(client, _valid("console_reply"))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}

    message = (await _messages(async_session_maker))["wamid.console"]
    assert message.author_kind == "console"
    assert message.bot_id is None


async def test_handover_changed_is_stored(client, async_session_maker):
    """AC1 + AC3: signed handover_changed -> stored on the contact, times as naive UTC."""
    await _seed(async_session_maker)
    resp = await _post(client, _valid("handover_changed"))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}

    contact = await _contact(async_session_maker)
    assert contact.wa_handover_active is True
    assert contact.wa_handover_expires_at == datetime(2026, 10, 12, 8, 30)
    assert contact.wa_handover_updated_at == datetime(2026, 10, 12, 8, 0)


async def test_delivery_status_is_stored(client, async_session_maker):
    """AC1 + AC3: signed delivery_status -> stored on the message."""
    await _seed(async_session_maker)
    resp = await _post(client, _valid("delivery_status"))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}

    message = (await _messages(async_session_maker))["wamid.out"]
    assert message.delivery_status == "delivered"


async def test_duplicate_and_ignored_results(client, async_session_maker):
    """AC3: the service's duplicate and ignored come back as the only key of the body."""
    await _seed(async_session_maker)
    assert (await _post(client, _valid("customer_message"))).json() == {"result": "stored"}

    again = await _post(client, _valid("customer_message"))
    assert again.status_code == 200
    assert again.json() == {"result": "duplicate"}

    unknown = _valid("handover_changed", visitor={"phone": OTHER_PHONE, "bsuid": None})
    ignored = await _post(client, unknown)
    assert ignored.status_code == 200
    assert ignored.json() == {"result": "ignored"}


async def test_event_id_is_not_stored(client, async_session_maker):
    """AC2: event_id is only logged; no column of any contact or message holds it."""
    await _seed(async_session_maker)
    await _post(client, _valid("customer_message", visitor=BSUID_ONLY))

    async with async_session_maker() as session:
        for model in (Contact, Message):
            for row in (await session.execute(select(model))).scalars().all():
                values = [getattr(row, c.key) for c in model.__table__.columns]
                assert EVENT_ID not in [str(v) for v in values]


# --- AC2: accepted variants ---


@pytest.mark.parametrize("bot_id", [None, _DROP])
async def test_ai_reply_bot_id_may_be_null_or_missing(client, async_session_maker, bot_id):
    """AC2: ai_reply's bot_id may be null or left out."""
    await _seed(async_session_maker)
    resp = await _post(client, _valid("ai_reply", bot_id=bot_id))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}
    assert (await _messages(async_session_maker))["wamid.ai"].bot_id is None


@pytest.mark.parametrize("error_code", [131026, "131026"])
async def test_error_code_may_be_integer_or_string(client, async_session_maker, error_code):
    """AC2: delivery_status error_code is an integer or a string."""
    await _seed(async_session_maker)
    status = {
        "wa_message_id": "wamid.out",
        "status": "failed",
        "error_code": error_code,
        "error_title": "Message undeliverable",
    }
    resp = await _post(client, _valid("delivery_status", status=status))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}

    message = (await _messages(async_session_maker))["wamid.out"]
    assert message.delivery_status == "failed"
    assert message.delivery_error == "131026 Message undeliverable"


async def test_handover_end_with_null_expiry_is_accepted(client, async_session_maker):
    """AC2 (9.5): active false with expires_at null is valid."""
    await _seed(async_session_maker)
    handover = {"active": False, "expires_at": None}
    resp = await _post(client, _valid("handover_changed", handover=handover))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}


async def test_visitor_with_only_bsuid_is_accepted(client, async_session_maker):
    """AC2: a visitor with a BSUID and no phone is valid."""
    resp = await _post(client, _valid("customer_message", visitor=BSUID_ONLY))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}


# --- AC1: signature and timestamp ---


async def test_one_changed_byte_is_401(client, async_session_maker):
    """AC1: the body differs by one byte from what was signed -> 401, nothing stored."""
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    headers = _sign(raw)
    tampered = raw.replace(b"hello", b"hellp")
    assert tampered != raw

    resp = await _post(client, tampered, headers)
    assert resp.status_code == 401
    await _unchanged(async_session_maker)


async def test_wrong_secret_is_401_even_for_a_valid_body(client, async_session_maker):
    """AC1: signed with another key -> 401, although the body itself is valid."""
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    resp = await _post(client, raw, _sign(raw, secret="some-other-key"))
    assert resp.status_code == 401
    await _unchanged(async_session_maker)


@pytest.mark.parametrize("offset", [-301, 301])
async def test_timestamp_301_seconds_off_is_401(client, async_session_maker, offset):
    """AC1: a timestamp 301 seconds before or after now -> 401."""
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    resp = await _post(client, raw, _sign(raw, ts=NOW + offset))
    assert resp.status_code == 401
    await _unchanged(async_session_maker)


@pytest.mark.parametrize("offset", [-300, 300])
async def test_timestamp_300_seconds_off_is_accepted(client, async_session_maker, offset):
    """AC1: "more than 300 seconds" is the limit, so exactly 300 still passes."""
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    resp = await _post(client, raw, _sign(raw, ts=NOW + offset))
    assert resp.status_code == 200


@pytest.mark.parametrize("drop", [(TS_HEADER,), (SIG_HEADER,), (TS_HEADER, SIG_HEADER)])
async def test_missing_header_is_401(client, async_session_maker, drop):
    """AC1: either signature header (or both) missing -> 401."""
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    headers = {k: v for k, v in _sign(raw).items() if k not in drop}
    resp = await _post(client, raw, headers)
    assert resp.status_code == 401
    await _unchanged(async_session_maker)


def _malformed(raw: bytes, case: str) -> dict:
    headers = _sign(raw)
    digest = headers["X-Acuven-Signature"].removeprefix("sha256=")
    if case == "no_prefix":
        headers["X-Acuven-Signature"] = digest
    elif case == "other_algorithm":
        headers["X-Acuven-Signature"] = f"sha1={digest}"
    elif case == "not_hex":
        headers["X-Acuven-Signature"] = "sha256=" + "z" * 64
    elif case == "short_digest":
        headers["X-Acuven-Signature"] = "sha256=" + digest[:-2]
    elif case == "timestamp_not_a_number":
        headers["X-Acuven-Timestamp"] = "now"
    elif case == "timestamp_fraction":
        headers["X-Acuven-Timestamp"] = f"{NOW}.5"
    return headers


@pytest.mark.parametrize(
    "case",
    [
        "no_prefix",
        "other_algorithm",
        "not_hex",
        "short_digest",
        "timestamp_not_a_number",
        "timestamp_fraction",
    ],
)
async def test_malformed_headers_are_401(client, async_session_maker, case):
    """AC1: signature not `sha256=<hex>` or timestamp not Unix seconds -> 401."""
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    resp = await _post(client, raw, _malformed(raw, case))
    assert resp.status_code == 401
    await _unchanged(async_session_maker)


async def test_previous_secret_is_accepted(client, async_session_maker, monkeypatch):
    """AC1: during rotation a body signed with CHATBOT_EVENTS_SECRET_PREVIOUS passes."""
    monkeypatch.setattr(settings, "chatbot_events_secret_previous", OLD_SECRET)
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    resp = await _post(client, raw, _sign(raw, secret=OLD_SECRET))
    assert resp.status_code == 200
    assert resp.json() == {"result": "stored"}


async def test_current_secret_still_accepted_alongside_previous(
    client, async_session_maker, monkeypatch
):
    """AC1: with both keys configured, the current one passes too."""
    monkeypatch.setattr(settings, "chatbot_events_secret_previous", OLD_SECRET)
    await _seed(async_session_maker)
    resp = await _post(client, _valid("customer_message"))
    assert resp.status_code == 200


@pytest.mark.parametrize("signed_with", [SECRET, None])
async def test_no_secret_configured_is_503(client, async_session_maker, monkeypatch, signed_with):
    """AC1: neither key configured -> 503, signed or not, and nothing stored."""
    monkeypatch.setattr(settings, "chatbot_events_secret", "")
    monkeypatch.setattr(settings, "chatbot_events_secret_previous", "")
    await _seed(async_session_maker)
    raw = _encode(_valid("customer_message"))
    headers = _sign(raw, secret=signed_with) if signed_with else {}
    resp = await _post(client, raw, headers)
    assert resp.status_code == 503
    await _unchanged(async_session_maker)


@pytest.mark.parametrize("raw", [b"not json", b"{}", _encode({"type": "nonsense"})])
@pytest.mark.parametrize("headers", ["none", "wrong_key"])
async def test_unsigned_bad_body_is_401_not_422(client, async_session_maker, raw, headers):
    """AC1: the signature is checked before the body is parsed: a bad body that is not
    correctly signed is 401, not 422."""
    signed = {} if headers == "none" else _sign(raw, secret="some-other-key")
    resp = await _post(client, raw, signed)
    assert resp.status_code == 401


# --- AC2: correctly signed bad bodies are 422 ---


def _bad_bodies() -> dict:
    cm, cr, hc, ds = "customer_message", "console_reply", "handover_changed", "delivery_status"
    expiry = "2026-10-12T08:30:00Z"
    active_no_expiry = {"active": True, "expires_at": None}
    inactive_with_expiry = {"active": False, "expires_at": expiry}
    active_not_boolean = {"active": "yes", "expires_at": expiry}
    unknown_status = {"wa_message_id": "wamid.out", "status": "seen"}
    code_list = {"wa_message_id": "wamid.out", "status": "failed", "error_code": [1]}
    return {
        "unknown_type": _valid(cm, type="customer_typing"),
        "type_missing": _valid(cm, type=_DROP),
        "occurred_at_naive": _valid(cm, occurred_at="2026-10-12T08:00:00"),
        "occurred_at_not_a_time": _valid(cm, occurred_at="yesterday"),
        "occurred_at_number": _valid(cm, occurred_at=1760256000),
        "occurred_at_missing": _valid(cm, occurred_at=_DROP),
        "visitor_missing": _valid(cm, visitor=_DROP),
        "visitor_no_phone_no_bsuid": _valid(cm, visitor={"phone": None, "bsuid": None}),
        "visitor_empty_phone_and_bsuid": _valid(cm, visitor={"phone": "", "bsuid": ""}),
        "visitor_bad_phone": _valid(cm, visitor={"phone": "12345", "bsuid": BSUID}),
        "customer_message_without_message": _valid(cm, message=_DROP),
        "ai_reply_without_body": _valid("ai_reply", message={"wa_message_id": "wamid.ai"}),
        "console_reply_without_id": _valid(cr, message={"body": "hi"}),
        "console_reply_empty_id": _valid(cr, message={"wa_message_id": "", "body": "hi"}),
        "handover_missing": _valid(hc, handover=_DROP),
        "handover_active_without_expiry": _valid(hc, handover=active_no_expiry),
        "handover_inactive_with_expiry": _valid(hc, handover=inactive_with_expiry),
        "handover_expires_at_key_missing": _valid(hc, handover={"active": True}),
        "handover_active_not_boolean": _valid(hc, handover=active_not_boolean),
        "status_missing": _valid(ds, status=_DROP),
        "status_unknown_value": _valid(ds, status=unknown_status),
        "status_without_message_id": _valid(ds, status={"status": "read"}),
        "status_error_code_list": _valid(ds, status=code_list),
    }


BAD_BODIES = _bad_bodies()


@pytest.mark.parametrize("name", sorted(BAD_BODIES))
async def test_signed_bad_body_is_422(client, async_session_maker, name):
    """AC2: a correctly signed body that breaks section 4 / 9.5 -> 422, nothing stored."""
    await _seed(async_session_maker)
    resp = await _post(client, BAD_BODIES[name])
    assert resp.status_code == 422
    await _unchanged(async_session_maker)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b""])
async def test_signed_non_object_body_is_422(client, async_session_maker, raw):
    """AC2: correctly signed but not a JSON object -> 422."""
    await _seed(async_session_maker)
    resp = await _post(client, raw)
    assert resp.status_code == 422
    await _unchanged(async_session_maker)


# --- AC3: other errors are not turned into 200 ---


async def test_processing_error_is_5xx(async_session_maker, monkeypatch):
    """AC3: an error inside the event handling is a 5xx, not a 200. Own client, so the
    app's error is answered as a response instead of being raised into the test."""

    async def _broken(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(chatbot_event_service, "process_event", _broken)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _post(client, _valid("customer_message"))
    assert resp.status_code >= 500
