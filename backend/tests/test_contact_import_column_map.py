"""`POST /api/contacts/import` with an optional `column_map` (CRM-TASK-055).

With a map, each column is read as the field the map gives it, whatever the
header says; without one the template headers apply exactly as before
(docs/contact-import-columns.md sections 2 and 4.1, section 9 items 14 to 17).
"""

import json
from io import BytesIO

import pytest
import pytest_asyncio
from openpyxl import Workbook
from sqlalchemy import func, select

from app.database import get_db
from app.main import app
from app.models.contact import Contact
from app.models.deal import Deal
from tests._people import ADMIN, logged_in_as, seed_people

SEEDED_CONTACTS = 3  # c-mine, c-theirs, c-orphan from seed_people

TEMPLATE_HEADERS = [
    "name",
    "company",
    "industry",
    "email",
    "phone",
    "address",
    "remark",
    "tag",
    "assigned_to_email",
    "status",
    "priority",
    "amount",
    "deal_title",
    "customer_id (add deal only)",
]


@pytest_asyncio.fixture
async def committing_client(client, async_session_maker):
    """`client`, but each request commits like the real `get_db`, so a refused
    import can be told apart from one that was only rolled back."""

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


async def _post(client, content: bytes, column_map=None):
    data = {}
    if column_map is not None:
        data["column_map"] = (
            column_map
            if isinstance(column_map, str)
            else json.dumps(column_map, ensure_ascii=False)
        )
    with logged_in_as(ADMIN):
        return await client.post(
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


async def _contact(session_maker, name: str) -> Contact:
    async with session_maker() as session:
        return (await session.execute(select(Contact).where(Contact.name == name))).scalar_one()


async def _contact_count(session_maker) -> int:
    async with session_maker() as session:
        return (await session.execute(select(func.count()).select_from(Contact))).scalar()


async def _deals_of(session_maker, contact_id: str) -> list[Deal]:
    async with session_maker() as session:
        return list(
            (await session.execute(select(Deal).where(Deal.contact_id == contact_id))).scalars()
        )


# --- 1: Chinese headers with a column_map ---


async def test_chinese_headers_land_in_mapped_fields(committing_client, async_session_maker):
    """AC1/AC2: a map over Chinese headers imports, each value in the field it was mapped to;
    notes, tags and deal_value are handled as the template's remark, tag and amount."""
    content = _xlsx(
        ["姓名", "公司", "邮箱", "地址", "备注", "标签", "状态", "金额", "商机名称"],
        [
            "陈大文",
            "大文五金",
            "chan@example.com",
            "吉隆坡",
            "老客户介绍",
            "VIP, 五金",
            "negotiating",
            "1200.5",
            "年度采购",
        ],
    )
    column_map = {
        "姓名": "name",
        "公司": "company",
        "邮箱": "email",
        "地址": "address",
        "备注": "notes",
        "标签": "tags",
        "状态": "status",
        "金额": "deal_value",
        "商机名称": "deal_title",
    }
    resp = await _post(committing_client, content, column_map)

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "total": 1,
        "inserted": 1,
        "deals_added": 0,
        "skipped": 0,
        "errors": [],
    }
    contact = await _contact(async_session_maker, "陈大文")
    assert contact.company == "大文五金"
    assert contact.email == "chan@example.com"
    assert contact.address == "吉隆坡"
    assert contact.notes == "老客户介绍"
    assert contact.tags == ["VIP", "五金"]
    (deal,) = await _deals_of(async_session_maker, contact.id)
    assert deal.amount == 1200.5
    assert deal.status == "negotiating"
    assert deal.title == "年度采购"


# --- 2: columns left out of the map, or mapped to null, are not imported ---


async def test_unmapped_and_null_columns_are_not_imported(committing_client, async_session_maker):
    """AC2: with a map only it is read -- a column missing from it (even a template
    header such as `email`) and a column mapped to null are both dropped."""
    content = _xlsx(
        ["姓名", "公司", "email", "行业"],
        ["林美玲", "美玲贸易", "lim@example.com", "Retail"],
    )
    resp = await _post(committing_client, content, {"姓名": "name", "公司": None})

    assert resp.status_code == 200
    assert resp.json()["data"]["inserted"] == 1
    contact = await _contact(async_session_maker, "林美玲")
    assert contact.company is None
    assert contact.email is None
    assert contact.industry is None


# --- 3: refused maps, and a map with only id and deal_value ---


@pytest.mark.parametrize(
    "column_map",
    [
        pytest.param('{"姓名": "name"', id="invalid-json"),
        pytest.param('["姓名", "name"]', id="not-an-object"),
        pytest.param({"姓名": "full_name"}, id="unknown-field"),
        pytest.param({"姓名": "name", "公司": "name"}, id="same-field-twice"),
        pytest.param({"公司": "company", "姓名": None}, id="neither-name-nor-id"),
        pytest.param({"姓名": "name", "传真": "phone"}, id="header-not-in-sheet"),
    ],
)
async def test_invalid_column_map_is_422_and_imports_nothing(
    committing_client, async_session_maker, column_map
):
    """AC1: invalid JSON, an unknown field, a field twice, neither name nor id, or a
    header the sheet's first row lacks -> 422 and no new customer."""
    content = _xlsx(["姓名", "公司"], ["王小明", "小明科技"])
    resp = await _post(committing_client, content, column_map)

    assert resp.status_code == 422
    assert resp.json()["success"] is False
    assert await _contact_count(async_session_maker) == SEEDED_CONTACTS


async def test_only_id_and_deal_value_adds_deals(committing_client, async_session_maker):
    """AC1 (section 9 item 14): a sheet of only customer id and amount needs no name column."""
    content = _xlsx(["客户编号", "金额"], ["c-mine", "800"])
    resp = await _post(committing_client, content, {"客户编号": "id", "金额": "deal_value"})

    assert resp.status_code == 200
    assert resp.json()["data"]["deals_added"] == 1
    assert resp.json()["data"]["inserted"] == 0
    assert [d.amount for d in await _deals_of(async_session_maker, "c-mine")] == [800.0]
    assert await _contact_count(async_session_maker) == SEEDED_CONTACTS


# --- 4: no column_map keeps the template headers ---


async def test_without_column_map_template_headers_apply(committing_client, async_session_maker):
    """AC3: no map -> the template headers are read as before; an unknown header is still
    dropped and `remark`/`tag`/`amount` still land in notes/tags/deal_value."""
    content = _xlsx(
        TEMPLATE_HEADERS + ["手机"],
        [
            "Tan Ah Kow",
            "Tan Hardware",
            "Retail",
            "tan@example.com",
            "",
            "Penang",
            "walk-in",
            "VIP,hardware",
            "",
            "lead",
            "high",
            "5000",
            "Shop fit-out",
            "",
            "0123456789",
        ],
        [""] * 9 + ["won", "", "300", "Top-up", "c-mine", ""],
    )
    resp = await _post(committing_client, content)

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "total": 2,
        "inserted": 1,
        "deals_added": 1,
        "skipped": 0,
        "errors": [],
    }
    contact = await _contact(async_session_maker, "Tan Ah Kow")
    assert contact.company == "Tan Hardware"
    assert contact.industry == "Retail"
    assert contact.email == "tan@example.com"
    assert contact.phone is None
    assert contact.address == "Penang"
    assert contact.notes == "walk-in"
    assert contact.tags == ["VIP", "hardware"]
    (deal,) = await _deals_of(async_session_maker, contact.id)
    assert (deal.amount, deal.priority, deal.title) == (5000.0, "high", "Shop fit-out")
    (added,) = await _deals_of(async_session_maker, "c-mine")
    assert (added.amount, added.status, added.title) == (300.0, "won", "Top-up")


# --- 5: a column mapped to id adds deals to existing customers ---


async def test_column_mapped_to_id_adds_deal_to_existing_customer(
    committing_client, async_session_maker
):
    """AC2: a column mapped to id is the template's customer_id -- the row adds a deal to
    that customer, and an unknown id is skipped as it is today."""
    content = _xlsx(
        ["姓名", "客户编号", "金额", "商机名称"],
        ["", "c-theirs", "450", "续约"],
        ["", "c-missing", "100", "不存在"],
    )
    resp = await _post(
        committing_client,
        content,
        {"姓名": "name", "客户编号": "id", "金额": "deal_value", "商机名称": "deal_title"},
    )

    data = resp.json()["data"]
    assert resp.status_code == 200
    assert (data["inserted"], data["deals_added"], data["skipped"]) == (0, 1, 1)
    assert data["errors"] == [
        {"row": 3, "field": "customer_id", "message": "Contact not found: c-missing"}
    ]
    (deal,) = await _deals_of(async_session_maker, "c-theirs")
    assert (deal.amount, deal.title) == (450.0, "续约")
    assert await _contact_count(async_session_maker) == SEEDED_CONTACTS


# --- 6: header matching ---


async def test_repeated_header_reads_the_first_column(committing_client, async_session_maker):
    """AC2: two text headers both `电话` -> only the first column is read."""
    content = _xlsx(["姓名", "电话", "电话"], ["黄志强", "first-col", "second-col"])
    resp = await _post(committing_client, content, {"姓名": "name", "电话": "notes"})

    assert resp.status_code == 200
    assert (await _contact(async_session_maker, "黄志强")).notes == "first-col"


async def test_numeric_header_is_skipped_for_the_text_one(committing_client, async_session_maker):
    """AC1: a number 2024 header ahead of a text `2024` header cannot be selected, so the
    map's `2024` reads the text column."""
    content = _xlsx(["姓名", 2024, "2024"], ["李国华", "numeric-col", "text-col"])
    resp = await _post(committing_client, content, {"姓名": "name", "2024": "notes"})

    assert resp.status_code == 200
    assert (await _contact(async_session_maker, "李国华")).notes == "text-col"


async def test_keys_equal_after_stripping_are_422(committing_client, async_session_maker):
    """AC1: two keys that are the same once the nine blank characters are stripped -> 422."""
    content = _xlsx(["姓名", "公司"], ["赵敏", "赵氏"])
    raw = json.dumps({"姓名": "name", "　公司 ": "company", "公司 ": None}, ensure_ascii=False)
    resp = await _post(committing_client, content, raw)

    assert resp.status_code == 422
    assert await _contact_count(async_session_maker) == SEEDED_CONTACTS


async def test_empty_column_map_uses_template_headers(committing_client, async_session_maker):
    """AC1/AC3: column_map sent as an empty string is treated as not given."""
    content = _xlsx(["name", "remark", "姓名"], ["Siti", "template-col", "ignored"])
    resp = await _post(committing_client, content, "")

    assert resp.status_code == 200
    assert resp.json()["data"]["inserted"] == 1
    assert (await _contact(async_session_maker, "Siti")).notes == "template-col"
