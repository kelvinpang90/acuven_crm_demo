"""`POST /api/contacts/import` skips rows that look like a customer already in
the CRM or a row earlier in the same sheet, unless told not to (CRM-TASK-062).

Same phone or same email only, normalized as duplicate_service does; the other
customer is never named (docs/contact-duplicate-hints.md 5.3, 6.3, 9.3, 9.9).
"""

import json
from datetime import datetime
from io import BytesIO

import pytest_asyncio
from openpyxl import Workbook
from sqlalchemy import func, select

from app.database import get_db
from app.main import app
from app.models.contact import Contact
from app.models.deal import Deal
from tests._people import ADMIN, logged_in_as, seed_people

SEEDED_CONTACTS = 3  # c-mine, c-theirs, c-orphan from seed_people
THEIRS_PHONE = "+60 12-222 2222"  # c-theirs ("Theirs") is stored as 60122222222


@pytest_asyncio.fixture
async def committing_client(client, async_session_maker):
    """`client`, but each request commits like the real `get_db`."""

    async def _get_db_committing():
        async with async_session_maker() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = _get_db_committing
    await seed_people(async_session_maker)
    yield client


def _xlsx(*rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _post(client, content: bytes, skip_duplicates=None, column_map=None):
    data = {}
    if skip_duplicates is not None:
        data["skip_duplicates"] = skip_duplicates
    if column_map is not None:
        data["column_map"] = json.dumps(column_map, ensure_ascii=False)
    with logged_in_as(ADMIN):
        resp = await client.post(
            "/api/contacts/import",
            files={
                "file": (
                    "customers.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data=data,
        )
    assert resp.status_code == 200
    return resp.json()["data"]


async def _add_contacts(session_maker, *contacts: Contact) -> None:
    async with session_maker() as session:
        session.add_all(contacts)
        await session.commit()


async def _contact_count(session_maker) -> int:
    async with session_maker() as session:
        return (await session.execute(select(func.count()).select_from(Contact))).scalar()


async def _names(session_maker) -> set[str]:
    async with session_maker() as session:
        return set((await session.execute(select(Contact.name))).scalars())


# --- 1: same phone as an existing customer ---


async def test_same_phone_as_existing_contact_is_skipped_without_naming_it(
    committing_client, async_session_maker
):
    """AC1: a new row whose normalized phone matches a non-deleted customer is skipped by
    default, reported on its sheet row as phone / same phone as an existing contact, and the
    error carries neither that customer's name nor its id."""
    data = await _post(committing_client, _xlsx(["name", "phone"], ["New Lead", THEIRS_PHONE]))

    assert (data["inserted"], data["skipped"]) == (0, 1)
    assert data["errors"] == [
        {
            "row": 2,
            "field": "phone",
            "message": "Possible duplicate: same phone as an existing contact",
        }
    ]
    assert "Theirs" not in data["errors"][0]["message"]
    assert "c-theirs" not in data["errors"][0]["message"]
    assert await _contact_count(async_session_maker) == SEEDED_CONTACTS


# --- 2: email compared case-insensitively ---


async def test_email_differing_only_in_case_is_skipped(committing_client, async_session_maker):
    """AC1: email is compared trimmed and lowercased, so a different case still counts."""
    await _add_contacts(
        async_session_maker, Contact(id="c-tan", name="Tan", email="Tan@Example.com")
    )
    data = await _post(
        committing_client, _xlsx(["name", "email"], ["Tan Again", "tan@EXAMPLE.com"])
    )

    assert (data["inserted"], data["skipped"]) == (0, 1)
    assert data["errors"] == [
        {
            "row": 2,
            "field": "email",
            "message": "Possible duplicate: same email as an existing contact",
        }
    ]


# --- 3: rows earlier in the same sheet, rule order, rows already invalid ---


async def test_same_phone_as_earlier_row_names_that_row(committing_client, async_session_maker):
    """AC1: the sheet's own inserted rows are compared too; row 3 repeating row 2's phone (in
    another notation) is skipped with same phone as row 2."""
    data = await _post(
        committing_client,
        _xlsx(["name", "phone"], ["First", "0123456789"], ["Second", "+60 12-345 6789"]),
    )

    assert (data["inserted"], data["skipped"]) == (1, 1)
    assert data["errors"] == [
        {"row": 3, "field": "phone", "message": "Possible duplicate: same phone as row 2"}
    ]
    assert "First" in await _names(async_session_maker)
    assert "Second" not in await _names(async_session_maker)


async def test_phone_rule_wins_over_email_and_only_one_error_is_recorded(
    committing_client, async_session_maker
):
    """AC1: the order is phone (sheet, existing) before email (sheet, existing) and only the
    first hit counts -- a phone matching an existing customer beats an email matching row 2."""
    data = await _post(
        committing_client,
        _xlsx(
            ["name", "email", "phone"],
            ["First", "shared@example.com", ""],
            ["Second", "shared@example.com", THEIRS_PHONE],
        ),
    )

    assert (data["inserted"], data["skipped"]) == (1, 1)
    assert data["errors"] == [
        {
            "row": 3,
            "field": "phone",
            "message": "Possible duplicate: same phone as an existing contact",
        }
    ]


async def test_row_failing_validation_gets_no_duplicate_error(
    committing_client, async_session_maker
):
    """AC1: a row already skipped for a missing name is not checked for duplicates."""
    data = await _post(committing_client, _xlsx(["name", "phone"], ["", THEIRS_PHONE]))

    assert data["skipped"] == 1
    assert data["errors"] == [{"row": 2, "field": "name", "message": "Name is required"}]


# --- 4: skip_duplicates false imports everything ---


async def test_skip_duplicates_false_inserts_every_row(committing_client, async_session_maker):
    """AC2/AC3: with skip_duplicates=false, rows matching an existing customer or an earlier
    row are inserted as before."""
    data = await _post(
        committing_client,
        _xlsx(
            ["name", "phone"],
            ["Copy Of Theirs", THEIRS_PHONE],
            ["Copy Again", THEIRS_PHONE],
        ),
        skip_duplicates="false",
    )

    assert data == {"total": 2, "inserted": 2, "deals_added": 0, "skipped": 0, "errors": []}
    assert await _contact_count(async_session_maker) == SEEDED_CONTACTS + 2


# --- 5: deleted customers do not count ---


async def test_deleted_contact_is_not_a_duplicate(committing_client, async_session_maker):
    """AC1: only non-deleted customers are compared."""
    await _add_contacts(
        async_session_maker,
        Contact(
            id="c-gone",
            name="Gone",
            phone="60177777777",
            email="gone@example.com",
            deleted_at=datetime.utcnow(),
        ),
    )
    data = await _post(
        committing_client,
        _xlsx(["name", "email", "phone"], ["Back Again", "gone@example.com", "0177777777"]),
    )

    assert (data["inserted"], data["skipped"], data["errors"]) == (1, 0, [])


# --- 6: rows with only an id are not checked ---


async def test_id_row_is_not_checked(committing_client, async_session_maker):
    """AC1: a row that adds a deal to an existing customer by id is never checked."""
    data = await _post(
        committing_client,
        _xlsx(
            ["name", "phone", "amount", "customer_id (add deal only)"],
            ["", THEIRS_PHONE, "300", "c-mine"],
        ),
    )

    assert data == {"total": 1, "inserted": 0, "deals_added": 1, "skipped": 0, "errors": []}
    async with async_session_maker() as session:
        deals = (await session.execute(select(Deal).where(Deal.contact_id == "c-mine"))).scalars()
        assert [d.amount for d in deals] == [300.0]


# --- 7: together with column_map ---


async def test_works_together_with_column_map(committing_client, async_session_maker):
    """AC3: skip_duplicates and CRM-TASK-055's column_map apply side by side -- mapped columns
    are checked, and skip_duplicates=false still imports them."""
    content = _xlsx(["姓名", "电话"], ["陈先生", THEIRS_PHONE])
    column_map = {"姓名": "name", "电话": "phone"}

    skipped = await _post(committing_client, content, column_map=column_map)
    assert (skipped["inserted"], skipped["skipped"]) == (0, 1)
    assert skipped["errors"] == [
        {
            "row": 2,
            "field": "phone",
            "message": "Possible duplicate: same phone as an existing contact",
        }
    ]

    imported = await _post(
        committing_client, content, skip_duplicates="false", column_map=column_map
    )
    assert (imported["inserted"], imported["skipped"], imported["errors"]) == (1, 0, [])
    assert "陈先生" in await _names(async_session_maker)
