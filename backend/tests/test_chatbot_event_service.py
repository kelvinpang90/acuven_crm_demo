"""ai_chatbot_demo event handling rules (docs/chatbot-events.md sections 5, 8 and 9.1, 9.4).

Each test names the acceptance criterion of CRM-TASK-011 it guards:
AC1 finding the contact, AC2 storing messages, AC3 handover, AC4 delivery status,
AC5 the entry point and the section 8 edge cases.
"""

import ast
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.user import User
from app.services import chatbot_event_service
from app.services.chatbot_event_service import process_event

T0 = datetime(2026, 10, 12, 8, 0, 0)
PHONE = "60123456789"
OTHER_PHONE = "60198765432"
BSUID = "MY.visitor-1"


def at(minutes: int) -> datetime:
    """An aware UTC occurred_at, `minutes` after T0."""
    return (T0 + timedelta(minutes=minutes)).replace(tzinfo=timezone.utc)


async def _seed(maker, *rows) -> None:
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _seed_sales(maker) -> None:
    """One active sales rep, so assign_contact has someone to pick."""
    sales = User(
        id="u-sales",
        name="Sales",
        email="sales@example.com",
        password_hash="x",
        role="sales",
    )
    await _seed(maker, sales)


async def _event(maker, event_type: str, **kwargs) -> str:
    kwargs.setdefault("occurred_at", at(0))
    async with maker() as session:
        result = await process_event(session, event_type=event_type, **kwargs)
        await session.commit()
    return result


async def _message_event(maker, wa_message_id: str = "wamid.1", **kwargs) -> str:
    kwargs.setdefault("event_type", "customer_message")
    kwargs.setdefault("body", "hello")
    return await _event(maker, wa_message_id=wa_message_id, **kwargs)


async def _all(maker, model) -> list:
    async with maker() as session:
        rows = (await session.execute(select(model))).scalars().all()
    return sorted(rows, key=lambda r: r.id)


async def _one(maker, model, *where):
    async with maker() as session:
        return (await session.execute(select(model).where(*where))).scalar_one()


async def _snapshot(maker) -> dict:
    """Every column of every contact, message and deal, to show nothing changed."""
    snapshot = {}
    for model in (Contact, Message, Deal):
        snapshot[model.__tablename__] = [
            {c.key: getattr(row, c.key) for c in model.__table__.columns}
            for row in await _all(maker, model)
        ]
    return snapshot


def _contact(id: str, **kwargs) -> Contact:
    kwargs.setdefault("name", id)
    return Contact(id=id, **kwargs)


def _sent_message(external_id: str = "wamid.out", **kwargs) -> Message:
    return Message(
        id=f"m-{external_id}",
        channel="whatsapp",
        direction="outbound",
        sender_id="ai_chatbot_demo",
        recipient_id=PHONE,
        body="hi",
        external_id=external_id,
        is_read=True,
        **kwargs,
    )


# --- AC1: finding the contact ---


async def test_event_phone_is_normalized_before_matching(async_session_maker):
    """AC1: the event's number is normalized first, so `+60 12-345 6789` finds `60123456789`."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    assert await _message_event(async_session_maker, phone="+60 12-345 6789") == "stored"

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == "c1"
    assert message.sender_id == PHONE
    assert len(await _all(async_session_maker, Contact)) == 1


async def test_several_contacts_on_one_number_take_the_earliest(async_session_maker):
    """AC1: several live contacts share the number -> the earliest created one."""
    await _seed(
        async_session_maker,
        _contact("c-late", phone=PHONE, created_at=T0),
        _contact("c-early", phone=PHONE, created_at=T0 - timedelta(days=1)),
    )
    await _message_event(async_session_maker, phone=PHONE)
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == "c-early"


async def test_same_creation_time_takes_the_smallest_id(async_session_maker):
    """AC1: equal created_at -> the smallest id."""
    await _seed(
        async_session_maker,
        _contact("c-b", phone=PHONE, created_at=T0),
        _contact("c-a", phone=PHONE, created_at=T0),
    )
    await _message_event(async_session_maker, phone=PHONE)
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == "c-a"


async def test_deleted_contact_is_not_matched_by_phone(async_session_maker):
    """AC1: only live contacts are matched; a deleted one with the number gets a new contact."""
    await _seed(async_session_maker, _contact("c-gone", phone=PHONE, deleted_at=T0))
    await _message_event(async_session_maker, phone=PHONE)
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id != "c-gone"


async def test_phone_match_fills_in_missing_bsuid(async_session_maker):
    """AC1: the number's contact has no BSUID and nobody holds it -> it is filled in."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    await _message_event(async_session_maker, phone=PHONE, bsuid=BSUID)
    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.whatsapp_bsuid == BSUID


async def test_phone_match_keeps_its_own_bsuid(async_session_maker):
    """AC1: a contact that already has a BSUID keeps it."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE, whatsapp_bsuid="MY.old"))
    await _message_event(async_session_maker, phone=PHONE, bsuid=BSUID)
    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.whatsapp_bsuid == "MY.old"


async def test_bsuid_match_fills_in_missing_phone(async_session_maker):
    """AC1: no contact has the number, the BSUID's contact has none -> the normalized number."""
    await _seed(async_session_maker, _contact("c1", whatsapp_bsuid=BSUID))
    await _message_event(async_session_maker, phone="+60 12 345 6789", bsuid=BSUID)

    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.phone == PHONE
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == "c1"


async def test_bsuid_match_keeps_its_own_phone(async_session_maker):
    """AC1: the BSUID's contact already has a (different) number -> it is not replaced."""
    await _seed(async_session_maker, _contact("c1", phone=OTHER_PHONE, whatsapp_bsuid=BSUID))
    await _message_event(async_session_maker, phone=PHONE, bsuid=BSUID)

    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.phone == OTHER_PHONE
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == "c1"


async def test_unnormalizable_phone_counts_as_no_phone(async_session_maker):
    """AC1: a number that cannot be normalized is treated as absent; the BSUID is used."""
    await _seed_sales(async_session_maker)
    await _message_event(async_session_maker, phone="12345", bsuid=BSUID)

    contact = await _one(async_session_maker, Contact, Contact.whatsapp_bsuid == BSUID)
    assert contact.phone is None
    assert contact.name == BSUID
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.sender_id == BSUID


async def test_no_usable_phone_and_no_bsuid_is_ignored(async_session_maker):
    """AC1: unnormalizable number and no BSUID -> ignored, nothing changes."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    before = await _snapshot(async_session_maker)

    result = await _message_event(async_session_maker, phone="12345", profile_name="Ann")
    assert result == "ignored"
    assert await _snapshot(async_session_maker) == before


async def test_new_contact_is_created_assigned_and_gets_a_lead(async_session_maker):
    """AC1: nobody found -> new contact (profile name, number, BSUID, not gateway),
    assigned through assign_contact, with one lead deal."""
    await _seed_sales(async_session_maker)
    await _message_event(
        async_session_maker,
        phone="0123456789",
        bsuid=BSUID,
        profile_name="Ann",
    )

    contact = await _one(async_session_maker, Contact, Contact.phone == PHONE)
    assert contact.name == "Ann"
    assert contact.whatsapp_bsuid == BSUID
    assert contact.is_gateway is False
    assert contact.assigned_to == "u-sales"
    deal = await _one(async_session_maker, Deal, Deal.contact_id == contact.id)
    assert deal.status == "lead"
    assert deal.assigned_to == "u-sales"


@pytest.mark.parametrize(
    ("phone", "bsuid", "profile_name", "expected"),
    [
        (PHONE, BSUID, "Ann", "Ann"),
        (PHONE, BSUID, None, PHONE),
        (None, BSUID, None, BSUID),
        (PHONE, None, "x" * 150, "x" * 100),
    ],
)
async def test_new_contact_name_fallbacks(
    async_session_maker, phone, bsuid, profile_name, expected
):
    """AC1: name is the profile name, else the number, else the BSUID, cut to 100 characters."""
    await _message_event(async_session_maker, phone=phone, bsuid=bsuid, profile_name=profile_name)
    (contact,) = await _all(async_session_maker, Contact)
    assert contact.name == expected


async def test_profile_name_does_not_rename_existing_contact(async_session_maker):
    """AC1: an existing contact's name is not overwritten by the profile name."""
    await _seed(async_session_maker, _contact("c1", name="Kept", phone=PHONE))
    await _message_event(async_session_maker, phone=PHONE, profile_name="WhatsApp Name")
    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.name == "Kept"


async def test_bsuid_held_only_by_deleted_contact_is_released_for_new_contact(
    async_session_maker,
):
    """AC1 (9.1): the BSUID sits on a soft-deleted contact -> taken off it, new contact uses it."""
    await _seed(async_session_maker, _contact("c-gone", whatsapp_bsuid=BSUID, deleted_at=T0))
    assert await _message_event(async_session_maker, bsuid=BSUID) == "stored"

    gone = await _one(async_session_maker, Contact, Contact.id == "c-gone")
    assert gone.whatsapp_bsuid is None
    fresh = await _one(async_session_maker, Contact, Contact.whatsapp_bsuid == BSUID)
    assert fresh.id != "c-gone"
    assert fresh.deleted_at is None


async def test_bsuid_held_only_by_deleted_contact_is_released_for_fill_in(async_session_maker):
    """AC1 (9.1): filling in a BSUID that a soft-deleted contact holds takes it off that one."""
    await _seed(
        async_session_maker,
        _contact("c-gone", whatsapp_bsuid=BSUID, deleted_at=T0),
        _contact("c1", phone=PHONE),
    )
    assert await _message_event(async_session_maker, phone=PHONE, bsuid=BSUID) == "stored"

    gone = await _one(async_session_maker, Contact, Contact.id == "c-gone")
    assert gone.whatsapp_bsuid is None
    live = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert live.whatsapp_bsuid == BSUID


# --- AC2: storing messages ---


async def test_duplicate_message_changes_nothing(async_session_maker):
    """AC2: same wa_message_id again -> duplicate, no data changes (not even BSUID fill-in)."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    await _message_event(async_session_maker, phone=PHONE, body="first")
    before = await _snapshot(async_session_maker)

    result = await _message_event(
        async_session_maker,
        phone=PHONE,
        bsuid=BSUID,
        body="second",
        occurred_at=at(5),
    )
    assert result == "duplicate"
    assert await _snapshot(async_session_maker) == before


async def test_customer_message_is_unread_inbound(async_session_maker):
    """AC2: customer_message -> unread inbound, author customer, visitor -> ai_chatbot_demo,
    owner from the contact, time from occurred_at."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE, assigned_to="u-owner"))
    result = await _message_event(async_session_maker, phone=PHONE, body="hi", occurred_at=at(3))
    assert result == "stored"

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.channel == "whatsapp"
    assert message.direction == "inbound"
    assert message.is_read is False
    assert message.author_kind == "customer"
    assert message.bot_id is None
    assert message.sender_id == PHONE
    assert message.recipient_id == "ai_chatbot_demo"
    assert message.assigned_to == "u-owner"
    assert message.body == "hi"
    assert message.created_at == T0 + timedelta(minutes=3)


@pytest.mark.parametrize("bot_id", ["bot-dental", None])
async def test_ai_reply_is_read_outbound_with_bot(async_session_maker, bot_id):
    """AC2: ai_reply -> read outbound, author ai, bot_id kept (may be empty)."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE, assigned_to="u-owner"))
    await _message_event(async_session_maker, event_type="ai_reply", phone=PHONE, bot_id=bot_id)

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.direction == "outbound"
    assert message.is_read is True
    assert message.author_kind == "ai"
    assert message.bot_id == bot_id
    assert message.sender_id == "ai_chatbot_demo"
    assert message.recipient_id == PHONE
    assert message.assigned_to == "u-owner"


async def test_console_reply_is_read_outbound(async_session_maker):
    """AC2: console_reply -> read outbound, author console."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    await _message_event(async_session_maker, event_type="console_reply", phone=PHONE)

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.direction == "outbound"
    assert message.is_read is True
    assert message.author_kind == "console"
    assert message.bot_id is None
    assert message.recipient_id == PHONE


async def test_visitor_side_falls_back_to_bsuid(async_session_maker):
    """AC2: no number -> the visitor side of the message is the BSUID."""
    await _message_event(async_session_maker, event_type="ai_reply", bsuid=BSUID)
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.recipient_id == BSUID
    assert message.sender_id == "ai_chatbot_demo"


async def test_occurred_at_is_stored_as_naive_utc(async_session_maker):
    """AC2: an occurred_at with an offset is stored as the same instant in naive UTC."""
    myt = timezone(timedelta(hours=8))
    await _message_event(
        async_session_maker,
        phone=PHONE,
        occurred_at=datetime(2026, 10, 12, 16, 0, 0, tzinfo=myt),
    )
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.created_at == datetime(2026, 10, 12, 8, 0, 0)
    assert message.created_at.tzinfo is None


async def test_reply_before_customer_message_still_creates_contact(async_session_maker):
    """AC2 / section 5: an ai_reply for an unknown visitor creates the contact too."""
    await _seed_sales(async_session_maker)
    await _message_event(async_session_maker, event_type="ai_reply", phone=PHONE)

    contact = await _one(async_session_maker, Contact, Contact.phone == PHONE)
    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == contact.id
    assert message.assigned_to == "u-sales"


# --- AC3: handover ---


async def _handover(maker, active: bool, minutes: int, expires_in: int = 30, **kwargs) -> str:
    kwargs.setdefault("phone", PHONE)
    return await _event(
        maker,
        "handover_changed",
        occurred_at=at(minutes),
        handover_active=active,
        handover_expires_at=at(minutes + expires_in) if active else None,
        **kwargs,
    )


async def test_handover_for_unknown_visitor_is_ignored(async_session_maker):
    """AC3: no contact found -> ignored, nothing created."""
    await _seed_sales(async_session_maker)
    before = await _snapshot(async_session_maker)
    assert await _handover(async_session_maker, True, 0, bsuid=BSUID) == "ignored"
    assert await _snapshot(async_session_maker) == before


async def test_handover_with_no_usable_identity_is_ignored(async_session_maker):
    """AC3: unnormalizable number and no BSUID -> ignored."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    assert await _handover(async_session_maker, True, 0, phone="12345") == "ignored"


async def test_handover_fills_in_nothing(async_session_maker):
    """AC3: handover finds the contact but fills in no BSUID and releases no deleted BSUID."""
    await _seed(
        async_session_maker,
        _contact("c-gone", whatsapp_bsuid=BSUID, deleted_at=T0),
        _contact("c1", phone=PHONE),
    )
    assert await _handover(async_session_maker, True, 0, bsuid=BSUID) == "stored"

    live = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert live.whatsapp_bsuid is None
    assert live.wa_handover_active is True
    gone = await _one(async_session_maker, Contact, Contact.id == "c-gone")
    assert gone.whatsapp_bsuid == BSUID


async def test_handover_by_bsuid_does_not_fill_in_phone(async_session_maker):
    """AC3: found by BSUID -> its missing number stays missing."""
    await _seed(async_session_maker, _contact("c1", whatsapp_bsuid=BSUID))
    assert await _handover(async_session_maker, True, 0, bsuid=BSUID) == "stored"

    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.phone is None
    assert contact.wa_handover_active is True


async def test_handover_start_and_end(async_session_maker):
    """AC3: start stores active, expiry and event time; end clears the expiry."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))

    assert await _handover(async_session_maker, True, 0) == "stored"
    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.wa_handover_active is True
    assert contact.wa_handover_expires_at == T0 + timedelta(minutes=30)
    assert contact.wa_handover_updated_at == T0

    assert await _handover(async_session_maker, False, 10) == "stored"
    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.wa_handover_active is False
    assert contact.wa_handover_expires_at is None
    assert contact.wa_handover_updated_at == T0 + timedelta(minutes=10)


async def test_handover_older_event_is_ignored(async_session_maker):
    """AC3: occurred_at earlier than the stored update time -> ignored, nothing changes."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    await _handover(async_session_maker, False, 10)
    before = await _snapshot(async_session_maker)

    assert await _handover(async_session_maker, True, 5) == "ignored"
    assert await _snapshot(async_session_maker) == before


async def test_handover_same_time_event_applies(async_session_maker):
    """AC3: occurred_at equal to the stored update time is not earlier -> applied."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    await _handover(async_session_maker, True, 10)

    assert await _handover(async_session_maker, False, 10) == "stored"
    contact = await _one(async_session_maker, Contact, Contact.id == "c1")
    assert contact.wa_handover_active is False


# --- AC4: delivery status ---


async def _status(maker, status: str, **kwargs) -> str:
    kwargs.setdefault("wa_message_id", "wamid.out")
    return await _event(maker, "delivery_status", status=status, **kwargs)


async def test_status_for_unknown_message_is_ignored(async_session_maker):
    """AC4: no message with that external_id -> ignored."""
    await _seed(async_session_maker, _sent_message("wamid.other"))
    before = await _snapshot(async_session_maker)
    assert await _status(async_session_maker, "read") == "ignored"
    assert await _snapshot(async_session_maker) == before


@pytest.mark.parametrize(
    ("current", "new", "expected"),
    [
        (None, "sent", "stored"),
        (None, "delivered", "stored"),
        (None, "read", "stored"),
        ("sent", "delivered", "stored"),
        ("sent", "read", "stored"),
        ("delivered", "read", "stored"),
        ("sent", "sent", "ignored"),
        ("delivered", "delivered", "ignored"),
        ("read", "read", "ignored"),
        ("delivered", "sent", "ignored"),
        ("read", "sent", "ignored"),
        ("read", "delivered", "ignored"),
        (None, "failed", "stored"),
        ("sent", "failed", "stored"),
        ("delivered", "failed", "stored"),
        ("read", "failed", "ignored"),
        ("failed", "sent", "ignored"),
        ("failed", "delivered", "ignored"),
        ("failed", "read", "ignored"),
        ("failed", "failed", "ignored"),
    ],
)
async def test_status_only_moves_up(async_session_maker, current, new, expected):
    """AC4: sent < delivered < read; failed only over none / sent / delivered; failed is final;
    a repeat is no upgrade. Ignored leaves the message untouched."""
    await _seed(
        async_session_maker,
        _sent_message(delivery_status=current, delivery_error="old" if current else None),
    )
    before = await _snapshot(async_session_maker)

    result = await _status(async_session_maker, new, error_code="131026", error_title="New")
    assert result == expected

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.out")
    if expected == "ignored":
        assert await _snapshot(async_session_maker) == before
    else:
        assert message.delivery_status == new


@pytest.mark.parametrize(
    ("error_code", "error_title", "expected"),
    [
        ("131026", "Message undeliverable", "131026 Message undeliverable"),
        (131026, "Message undeliverable", "131026 Message undeliverable"),
        ("131026", None, "131026"),
        (None, "Message undeliverable", "Message undeliverable"),
        (None, None, None),
        ("", "", None),
        ("1", "x" * 400, "1 " + "x" * 298),
    ],
)
async def test_failed_stores_code_and_title(async_session_maker, error_code, error_title, expected):
    """AC4: failed stores "code title", or the one present, or nothing; cut to 300 characters."""
    await _seed(async_session_maker, _sent_message(delivery_status="sent"))
    result = await _status(
        async_session_maker,
        "failed",
        error_code=error_code,
        error_title=error_title,
    )
    assert result == "stored"

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.out")
    assert message.delivery_status == "failed"
    assert message.delivery_error == expected


async def test_other_statuses_leave_delivery_error_alone(async_session_maker):
    """AC4: a non-failed status does not touch delivery_error."""
    await _seed(async_session_maker, _sent_message(delivery_status="sent", delivery_error="keep"))
    assert await _status(async_session_maker, "read", error_code="1", error_title="t") == "stored"

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.out")
    assert message.delivery_status == "read"
    assert message.delivery_error == "keep"


# --- AC5: entry point and section 8 edge cases ---


def test_entry_point_takes_plain_values_only():
    """AC5: the service imports no request models, routers, web framework or HTTP client."""
    modules = set()
    for node in ast.walk(ast.parse(inspect.getsource(chatbot_event_service))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
    for forbidden in ("app.schemas", "app.routers", "fastapi", "httpx", "requests"):
        assert not [m for m in modules if m == forbidden or m.startswith(forbidden + ".")]


async def test_unknown_event_type_is_refused(async_session_maker):
    """AC5: the entry point only accepts the five event types."""
    with pytest.raises(ValueError):
        await _event(async_session_maker, "something_else")


async def test_edge_bsuid_visitor_later_shares_someone_elses_number(async_session_maker):
    """AC5 / section 8, case 1: a BSUID-only visitor shares a number another contact has ->
    the message goes to the number's contact, the taken BSUID is not filled in, both remain."""
    await _seed(
        async_session_maker,
        _contact("c-bsuid", whatsapp_bsuid=BSUID),
        _contact("c-phone", phone=PHONE),
    )
    assert await _message_event(async_session_maker, phone=PHONE, bsuid=BSUID) == "stored"

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == "c-phone"
    phone_contact = await _one(async_session_maker, Contact, Contact.id == "c-phone")
    assert phone_contact.whatsapp_bsuid is None
    bsuid_contact = await _one(async_session_maker, Contact, Contact.id == "c-bsuid")
    assert bsuid_contact.whatsapp_bsuid == BSUID
    assert bsuid_contact.phone is None
    assert len(await _all(async_session_maker, Contact)) == 2


async def test_edge_number_belongs_to_gateway_contact(async_session_maker):
    """AC5 / section 8, case 2: the number is an is_gateway contact's -> the message is
    attached to it, it stays is_gateway, no new contact or deal."""
    await _seed(async_session_maker, _contact("c-gw", phone=PHONE, is_gateway=True))
    assert await _message_event(async_session_maker, phone=PHONE, profile_name="Ann") == "stored"

    message = await _one(async_session_maker, Message, Message.external_id == "wamid.1")
    assert message.contact_id == "c-gw"
    contact = await _one(async_session_maker, Contact, Contact.id == "c-gw")
    assert contact.is_gateway is True
    assert contact.name == "c-gw"
    assert len(await _all(async_session_maker, Contact)) == 1
    assert await _all(async_session_maker, Deal) == []


async def test_edge_replayed_old_events_do_not_break_state(async_session_maker):
    """AC5 / section 8, case 3: dead-letter replays of a message, an old handover and an old
    status change nothing."""
    await _seed(async_session_maker, _contact("c1", phone=PHONE))
    await _message_event(async_session_maker, phone=PHONE, occurred_at=at(0))
    await _handover(async_session_maker, True, 1)
    await _handover(async_session_maker, False, 2)
    await _event(async_session_maker, "ai_reply", wa_message_id="wamid.out", phone=PHONE, body="a")
    await _status(async_session_maker, "delivered")
    await _status(async_session_maker, "read")
    before = await _snapshot(async_session_maker)

    assert await _message_event(async_session_maker, phone=PHONE) == "duplicate"
    assert await _handover(async_session_maker, True, 1) == "ignored"
    assert await _status(async_session_maker, "delivered") == "ignored"
    assert await _status(async_session_maker, "failed", error_code="1") == "ignored"
    assert await _snapshot(async_session_maker) == before
