"""Phone numbers on the save paths, the conflict query and inbound matching.

CRM-TASK-007 (docs/chatbot-events.md sections 3, 9.2 and P2):
- AC2: contact create, edit, sheet import and the AutoCount customer sync store
  the normalized number, keep what cannot be normalized as is, leave contacts
  without a number alone, and the sync does not rewrite an unchanged number.
- AC3: the conflict query returns, in normalized form, the given numbers that
  two or more non-deleted contacts share.
- AC4: the Meta webhook and the gateway inbound attach a shared number's
  message to the earliest created contact (lowest id on a tie) without erroring.
"""

from datetime import datetime

import pytest
from sqlalchemy import func, select, update

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import autocount_client, autocount_service, contact_service, whatsapp_service
from tests._people import ADMIN, REP

# --- AC2: create ---


async def _create(session_maker, phone) -> Contact:
    data = {"name": "New Customer"}
    if phone is not ...:
        data["phone"] = phone
    async with session_maker() as session:
        created = await contact_service.create_contact(session, data, REP)
        await session.commit()
    return await _contact(session_maker, created["id"])


async def _contact(session_maker, contact_id: str) -> Contact:
    async with session_maker() as session:
        result = await session.execute(select(Contact).where(Contact.id == contact_id))
        return result.scalar_one()


async def test_create_stores_the_normalized_number(async_session_maker):
    """AC2: a new contact's number is stored normalized."""
    contact = await _create(async_session_maker, "+60 12-345 6789")
    assert contact.phone == "60123456789"


@pytest.mark.parametrize("phone", ["12345", "0000123456789", ""])
async def test_create_keeps_what_cannot_be_normalized(async_session_maker, phone):
    """AC2: a number that cannot be normalized is stored exactly as entered."""
    contact = await _create(async_session_maker, phone)
    assert contact.phone == phone


async def test_create_without_a_number_is_unchanged(async_session_maker):
    """AC2: no phone in the request still stores no phone."""
    contact = await _create(async_session_maker, ...)
    assert contact.phone is None


# --- AC2: edit ---


async def _seed_contact(session_maker, phone) -> None:
    async with session_maker() as session:
        session.add(Contact(id="c-edit", name="Edit Me", phone=phone))
        await session.commit()


async def _update(session_maker, data: dict) -> Contact:
    async with session_maker() as session:
        await contact_service.update_contact(session, "c-edit", data, ADMIN)
        await session.commit()
    return await _contact(session_maker, "c-edit")


async def test_edit_stores_the_normalized_number(async_session_maker):
    """AC2: an edited number is stored normalized."""
    await _seed_contact(async_session_maker, "60111111111")
    contact = await _update(async_session_maker, {"phone": "012-345 6789"})
    assert contact.phone == "60123456789"


async def test_edit_keeps_what_cannot_be_normalized(async_session_maker):
    """AC2: an edited number that cannot be normalized is stored as entered."""
    await _seed_contact(async_session_maker, "60111111111")
    contact = await _update(async_session_maker, {"phone": "ext. 12"})
    assert contact.phone == "ext. 12"


async def test_edit_without_a_number_leaves_it_alone(async_session_maker):
    """AC2: an edit that does not carry phone does not touch the stored one."""
    await _seed_contact(async_session_maker, "012-345 6789")
    contact = await _update(async_session_maker, {"name": "Renamed"})
    assert contact.name == "Renamed"
    assert contact.phone == "012-345 6789"


# --- AC2: sheet import ---


async def _import_one(session_maker, row: dict) -> Contact:
    async with session_maker() as session:
        await contact_service.import_contacts(session, [row], ADMIN)
        await session.commit()
    async with session_maker() as session:
        result = await session.execute(select(Contact).where(Contact.name == row["name"]))
        return result.scalar_one()


async def test_import_stores_the_normalized_number(async_session_maker):
    """AC2: an imported number is stored normalized."""
    contact = await _import_one(async_session_maker, {"name": "Row", "phone": "0060123456789"})
    assert contact.phone == "60123456789"


async def test_import_keeps_what_cannot_be_normalized(async_session_maker):
    """AC2: an imported number that cannot be normalized is stored as in the sheet."""
    contact = await _import_one(async_session_maker, {"name": "Row", "phone": "1234"})
    assert contact.phone == "1234"


@pytest.mark.parametrize("row", [{"name": "Row"}, {"name": "Row", "phone": ""}])
async def test_import_without_a_number_is_unchanged(async_session_maker, row):
    """AC2: a missing or blank phone cell still stores no phone."""
    contact = await _import_one(async_session_maker, row)
    assert contact.phone is None


# --- AC2: AutoCount customer sync ---


def _debtor(phone) -> dict:
    return {"AccNo": "300-A001", "CompanyName": "Acme Sdn Bhd", "Phone1": phone}


@pytest.fixture
def debtors(monkeypatch):
    """What AutoCount's debtor listing returns; tests set `pulled[:]`."""
    pulled: list[dict] = []

    async def _get_all_debtors(active_only: bool = True) -> list[dict]:
        return pulled

    monkeypatch.setattr(autocount_client, "get_all_debtors", _get_all_debtors)
    return pulled


async def _sync(session_maker) -> Contact:
    async with session_maker() as session:
        await autocount_service._sync_customers(session)
        await session.commit()
    async with session_maker() as session:
        query = select(Contact).where(Contact.autocount_customer_code == "300-A001")
        return (await session.execute(query)).scalar_one()


async def test_sync_stores_the_normalized_number(async_session_maker, debtors):
    """AC2 (section 9.2): AutoCount's raw number is stored normalized."""
    debtors[:] = [_debtor("+60 12-345 6789")]
    contact = await _sync(async_session_maker)
    assert contact.phone == "60123456789"


async def test_sync_keeps_what_cannot_be_normalized(async_session_maker, debtors):
    """AC2: an AutoCount number that cannot be normalized is stored as pulled."""
    debtors[:] = [_debtor("03-1234")]
    contact = await _sync(async_session_maker)
    assert contact.phone == "03-1234"


@pytest.mark.parametrize("phone", [None, ""])
async def test_sync_without_a_number_is_unchanged(async_session_maker, debtors, phone):
    """AC2: no Phone1 in AutoCount still stores no phone."""
    debtors[:] = [_debtor(phone)]
    contact = await _sync(async_session_maker)
    assert contact.phone is None


@pytest.mark.parametrize("pulled", ["012-345 6789", "60123456789"])
async def test_sync_pulling_the_same_number_writes_nothing(async_session_maker, debtors, pulled):
    """AC2 (section 9.2): pulling the same number again leaves the row untouched.

    `updated_at` moves only when an UPDATE is issued (`onupdate`), so a pinned
    value surviving the second run means the row was not rewritten.
    """
    debtors[:] = [_debtor(pulled)]
    first = await _sync(async_session_maker)
    assert first.phone == "60123456789"

    pinned = datetime(2020, 1, 1)
    async with async_session_maker() as session:
        await session.execute(
            update(Contact).where(Contact.id == first.id).values(updated_at=pinned)
        )
        await session.commit()

    second = await _sync(async_session_maker)
    assert second.phone == "60123456789"
    assert second.updated_at == pinned


# --- AC3: conflict query ---


async def _seed(session_maker, *contacts: Contact) -> None:
    async with session_maker() as session:
        session.add_all(contacts)
        await session.commit()


async def _conflicts(session_maker, phones) -> set[str]:
    async with session_maker() as session:
        return await contact_service.find_phone_conflicts(session, phones)


async def test_shared_number_is_a_conflict_in_normalized_form(async_session_maker):
    """AC3: a number two contacts share is returned, normalized, whatever form was asked."""
    await _seed(
        async_session_maker,
        Contact(id="c-1", name="One", phone="60123456789"),
        Contact(id="c-2", name="Two", phone="60123456789"),
        Contact(id="c-3", name="Three", phone="60199999999"),
    )
    conflicts = await _conflicts(async_session_maker, ["+60 12-345 6789", "60199999999"])
    assert conflicts == {"60123456789"}


async def test_three_contacts_on_one_number_is_one_conflict(async_session_maker):
    """AC3: "two or more" -- three sharing a number is still reported once."""
    await _seed(
        async_session_maker,
        *[Contact(id=f"c-{i}", name=str(i), phone="60123456789") for i in range(3)],
    )
    conflicts = await _conflicts(async_session_maker, ["012-345 6789", "60123456789"])
    assert conflicts == {"60123456789"}


async def test_stored_value_is_compared_as_is(async_session_maker):
    """AC3: stored numbers are not normalized by the query (CRM-TASK-009 does that)."""
    await _seed(
        async_session_maker,
        Contact(id="c-1", name="One", phone="60123456789"),
        Contact(id="c-2", name="Two", phone="012-345 6789"),
    )
    assert await _conflicts(async_session_maker, ["60123456789"]) == set()


async def test_unnormalizable_and_blank_numbers_never_conflict(async_session_maker):
    """AC3: shared blank, empty or unnormalizable values are not conflicts."""
    await _seed(
        async_session_maker,
        Contact(id="c-1", name="One", phone="12345"),
        Contact(id="c-2", name="Two", phone="12345"),
        Contact(id="c-3", name="Three", phone=""),
        Contact(id="c-4", name="Four", phone=""),
        Contact(id="c-5", name="Five", phone=None),
        Contact(id="c-6", name="Six", phone=None),
    )
    assert await _conflicts(async_session_maker, ["12345", "", None, "abc"]) == set()


async def test_no_numbers_asked_is_no_conflict(async_session_maker):
    """AC3: an empty request answers empty without querying for everything."""
    await _seed(
        async_session_maker,
        Contact(id="c-1", name="One", phone="60123456789"),
        Contact(id="c-2", name="Two", phone="60123456789"),
    )
    assert await _conflicts(async_session_maker, []) == set()


async def test_conflict_goes_away_when_a_number_is_changed(async_session_maker):
    """AC3: editing one of the pair to another number clears the conflict."""
    await _seed(
        async_session_maker,
        Contact(id="c-1", name="One", phone="60123456789"),
        Contact(id="c-edit", name="Two", phone="60123456789"),
    )
    assert await _conflicts(async_session_maker, ["60123456789"]) == {"60123456789"}

    await _update(async_session_maker, {"phone": "019-999 9999"})

    assert await _conflicts(async_session_maker, ["60123456789", "60199999999"]) == set()


async def test_conflict_goes_away_when_a_contact_is_deleted(async_session_maker):
    """AC3: soft-deleting one of the pair clears the conflict."""
    await _seed(
        async_session_maker,
        Contact(id="c-1", name="One", phone="60123456789"),
        Contact(id="c-2", name="Two", phone="60123456789"),
    )
    async with async_session_maker() as session:
        assert await contact_service.soft_delete_contact(session, "c-2")
        await session.commit()

    assert await _conflicts(async_session_maker, ["60123456789"]) == set()


# --- AC4: inbound attaches to the earliest created contact ---

OLD = datetime(2026, 1, 1, 8, 0, 0)
NEW = datetime(2026, 6, 1, 8, 0, 0)


async def _seed_twins(session_maker, phone: str, same_time: bool) -> None:
    """Two contacts on one number; the earlier one has the larger id when times differ."""
    if same_time:
        twins = [
            Contact(id="c-b", name="B", phone=phone, created_at=OLD),
            Contact(id="c-a", name="A", phone=phone, created_at=OLD),
        ]
    else:
        twins = [
            Contact(id="c-a", name="A", phone=phone, created_at=NEW),
            Contact(id="c-z", name="Z", phone=phone, created_at=OLD),
        ]
    await _seed(session_maker, *twins)


async def _message_owner(session_maker, external_id: str) -> str:
    async with session_maker() as session:
        query = select(Message.contact_id).where(Message.external_id == external_id)
        return (await session.execute(query)).scalar_one()


async def _contact_count(session_maker) -> int:
    async with session_maker() as session:
        return (await session.execute(select(func.count()).select_from(Contact))).scalar()


def _wa_message(external_id: str) -> dict:
    return {"from": "60123456789", "id": external_id, "type": "text", "text": {"body": "hi"}}


@pytest.mark.parametrize(("same_time", "expected"), [(False, "c-z"), (True, "c-a")])
async def test_meta_webhook_uses_the_earliest_contact(async_session_maker, same_time, expected):
    """AC4: the Meta webhook stores the message on the earliest created contact
    (lowest id on a tie) and creates no new one."""
    await _seed_twins(async_session_maker, "60123456789", same_time)
    payload = {"entry": [{"changes": [{"value": {"messages": [_wa_message("wamid.META1")]}}]}]}

    async with async_session_maker() as session:
        await whatsapp_service.process_inbound(session, payload)

    assert await _message_owner(async_session_maker, "wamid.META1") == expected
    assert await _contact_count(async_session_maker) == 2


@pytest.mark.parametrize(("same_time", "expected"), [(False, "c-z"), (True, "c-a")])
async def test_gateway_inbound_uses_the_earliest_contact(
    client, async_session_maker, monkeypatch, same_time, expected
):
    """AC4: the gateway inbound does the same and still answers 200 with its receipt."""
    monkeypatch.setattr(settings, "internal_shared_secret", "test-secret")
    await _seed_twins(async_session_maker, "60123456789", same_time)

    resp = await client.post(
        "/internal/whatsapp/inbound",
        json={"message": _wa_message("wamid.GW1")},
        headers={"X-Internal-Secret": "test-secret"},
    )

    assert resp.status_code == 200
    assert len(resp.json()["messages"]) == 1
    assert await _message_owner(async_session_maker, "wamid.GW1") == expected
    assert await _contact_count(async_session_maker) == 2


async def test_deleted_earlier_contact_is_skipped(async_session_maker):
    """AC4: only non-deleted contacts are candidates, as before."""
    await _seed_twins(async_session_maker, "60123456789", same_time=False)
    async with async_session_maker() as session:
        await session.execute(
            update(Contact).where(Contact.id == "c-z").values(deleted_at=datetime(2026, 7, 1))
        )
        await session.commit()
    payload = {"entry": [{"changes": [{"value": {"messages": [_wa_message("wamid.META2")]}}]}]}

    async with async_session_maker() as session:
        await whatsapp_service.process_inbound(session, payload)

    assert await _message_owner(async_session_maker, "wamid.META2") == "c-a"
