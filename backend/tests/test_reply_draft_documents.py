"""CRM-TASK-079 tests for AutoCount documents in the reply-draft request."""

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.ai_request import AiRequest
from app.models.autocount_document import AutocountDocument
from app.models.contact import Contact
from app.models.message import Message
from app.services import ai_api_client, ai_assist_service
from app.services.ai_features import (
    EMAIL_DRAFT_SCHEMA,
    REPLY_DRAFT_SYSTEM,
    WHATSAPP_DRAFT_SCHEMA,
)

SYNCED_AT = datetime(2026, 10, 27, 2, 0, 0, 123456)


def _document(document_id, **values):
    fields = {
        "id": document_id,
        "doc_type": "invoice",
        "doc_no": document_id,
        "customer_code": "C-1",
        "contact_id": "contact-1",
        "status": "open",
        "total_amount": Decimal("100"),
        "currency_code": "MYR",
        "doc_date": date(2026, 10, 1),
        "synced_at": SYNCED_AT,
    }
    fields.update(values)
    return AutocountDocument(**fields)


def _quotation(document_id, **values):
    return _document(document_id, doc_type="quotation", **values)


def _line(**values):
    fields = {
        "product_code": "P-1",
        "description": "Chiller",
        "qty": 1,
        "unit": "unit",
        "unit_price": "10",
        "discount_amt": "0",
        "sub_total": "10",
    }
    fields.update(values)
    return fields


async def _seed(maker, documents=(), with_message=True):
    async with maker() as session:
        session.add(Contact(id="contact-1", name="Name"))
        session.add(Contact(id="contact-2", name="Other"))
        if with_message:
            session.add(
                Message(
                    id="message-1",
                    contact_id="contact-1",
                    channel="whatsapp",
                    direction="inbound",
                    sender_id="sender",
                    recipient_id="recipient",
                    body="How much?",
                )
            )
        session.add_all(list(documents))
        await session.commit()


def _client(monkeypatch, calls, output=None):
    monkeypatch.setattr(ai_assist_service.ai_api_client, "is_ai_configured", lambda: True)

    async def send_message(**kwargs):
        calls.append(kwargs)
        return ai_api_client.AiApiResult(
            "ok", output or {"draft": "Reply", "note_for_agent": ""}, "draft-1"
        )

    monkeypatch.setattr(ai_assist_service.ai_api_client, "send_message", send_message)


async def _draft_input(maker, monkeypatch, documents=(), channel="whatsapp", instruction=None):
    await _seed(maker, documents)
    calls = []
    output = {"draft": "Reply", "note_for_agent": ""}
    if channel == "email":
        output["subject"] = "Subject"
    _client(monkeypatch, calls, output)
    async with maker() as session:
        result = await ai_assist_service.generate_reply_draft(
            session,
            user_id="user-1",
            contact_id="contact-1",
            channel=channel,
            output_language="en",
            instruction=instruction,
        )
    assert result.category == "ok"
    assert len(calls) == 1
    return json.loads(calls[0]["messages"][0]["content"]), calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("with_sixth", "expected_order", "expected_truncated"),
    [
        (True, ["a2", "a1", "a3", "a4", "a6"], True),
        (False, ["a2", "a1", "a3", "a4", "a5"], False),
    ],
)
async def test_selection_keeps_newest_five_with_null_dates_last(
    async_session_maker, monkeypatch, with_sixth, expected_order, expected_truncated
):
    """Guards criterion 1 (selection): newest 5, null doc_date last, same date by id desc,
    other contacts excluded, documents_truncated only when more than 5."""
    documents = [
        _document("a1", doc_date=date(2026, 10, 5)),
        _document("a2", doc_date=date(2026, 10, 5)),
        _document("a3", doc_date=date(2026, 10, 3)),
        _document("a4", doc_date=date(2026, 9, 1)),
        _document("a5", doc_date=None),
        _document("z9", doc_date=date(2026, 10, 30), contact_id="contact-2"),
    ]
    if with_sixth:
        documents.append(_document("a6", doc_date=date(2026, 8, 1)))

    payload, _ = await _draft_input(async_session_maker, monkeypatch, documents)

    assert [item["doc_no"] for item in payload["documents"]] == expected_order
    assert payload["documents_truncated"] is expected_truncated


@pytest.mark.asyncio
async def test_invoice_and_quotation_have_exactly_their_fields(async_session_maker, monkeypatch):
    """Guards criterion 1 (fields): invoice and quotation keys, two-decimal amounts,
    null outstanding, dates, and synced_at in UTC seconds with Z; no ids or codes."""
    documents = [
        _document(
            "iv-1",
            doc_no="IV-0045",
            total_amount=Decimal("3500"),
            outstanding_amount=Decimal("1500.5"),
            doc_date=date(2026, 9, 30),
            due_date=date(2026, 10, 30),
        ),
        _document("iv-2", doc_no="IV-0040", doc_date=date(2026, 9, 1), outstanding_amount=None),
        _quotation(
            "qt-1",
            doc_no="QT-0012",
            total_amount=Decimal("8600"),
            doc_date=date(2026, 10, 3),
            validity="30 days",
            line_items=[
                _line(
                    description="双门冷藏展示柜",
                    qty="2",
                    unit_price="4300",
                    discount_amt="0",
                    sub_total="8600",
                )
            ],
        ),
    ]

    payload, _ = await _draft_input(async_session_maker, monkeypatch, documents)

    assert payload["documents"] == [
        {
            "type": "quotation",
            "doc_no": "QT-0012",
            "status": "open",
            "currency": "MYR",
            "total": "8600.00",
            "doc_date": "2026-10-03",
            "synced_at": "2026-10-27T02:00:00Z",
            "validity": "30 days",
            "lines": [
                {
                    "description": "双门冷藏展示柜",
                    "qty": "2",
                    "unit": "unit",
                    "unit_price": "4300.00",
                    "discount": "0.00",
                    "sub_total": "8600.00",
                }
            ],
        },
        {
            "type": "invoice",
            "doc_no": "IV-0045",
            "status": "open",
            "currency": "MYR",
            "total": "3500.00",
            "doc_date": "2026-09-30",
            "synced_at": "2026-10-27T02:00:00Z",
            "outstanding": "1500.50",
            "due_date": "2026-10-30",
        },
        {
            "type": "invoice",
            "doc_no": "IV-0040",
            "status": "open",
            "currency": "MYR",
            "total": "100.00",
            "doc_date": "2026-09-01",
            "synced_at": "2026-10-27T02:00:00Z",
            "outstanding": None,
            "due_date": None,
        },
    ]
    assert payload["documents_truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quotations", "expected_line_counts", "expected_truncated"),
    [
        ([{"line_items": [_line()] * 10}], [10], False),
        ([{"line_items": [_line()] * 11}], [10], True),
        (
            [
                {"doc_date": date(2026, 10, 3), "line_items": [_line()] * 10},
                {"doc_date": date(2026, 10, 2), "line_items": [_line()] * 10},
                {"doc_date": date(2026, 10, 1), "line_items": [_line()] * 10},
            ],
            [10, 10, 0],
            True,
        ),
        ([{"line_items": [_line(description="价" * 60)]}], [1], False),
        ([{"line_items": [_line(description="价" * 61)]}], [1], True),
        ([{"line_items": [_line(unit="件" * 10)]}], [1], False),
        ([{"line_items": [_line(unit="件" * 11)]}], [1], True),
        ([{"validity": "天" * 100, "line_items": []}], [0], False),
        ([{"validity": "天" * 101, "line_items": []}], [0], True),
        ([{"line_items": None}], [0], False),
        ([{"line_items": ["x", 5, None, [1]] + [_line()] * 10}], [10], False),
    ],
)
async def test_quotation_lines_limits_and_truncation(
    async_session_maker, monkeypatch, quotations, expected_line_counts, expected_truncated
):
    """Guards criteria 2 and 3 (lines): 10 per quotation, 20 in total in document order,
    code-point truncation of description, unit and validity, null line_items, and skipped
    non-object elements that do not count towards the limits."""
    documents = [_quotation(f"q{index}", **values) for index, values in enumerate(quotations)]

    payload, _ = await _draft_input(async_session_maker, monkeypatch, documents)

    assert [len(item["lines"]) for item in payload["documents"]] == expected_line_counts
    assert payload["documents_truncated"] is expected_truncated
    for item in payload["documents"]:
        assert item["validity"] is None or len(item["validity"]) <= 100
        for line in item["lines"]:
            assert set(line) == {
                "description",
                "qty",
                "unit",
                "unit_price",
                "discount",
                "sub_total",
            }
            assert len(line["description"]) <= 60
            assert line["unit"] is None or len(line["unit"]) <= 10


@pytest.mark.asyncio
async def test_line_text_fields_are_cut_or_defaulted(async_session_maker, monkeypatch):
    """Guards criterion 2 (lines): description cut to 60 code points or empty string,
    unit cut to 10 code points or null, validity cut to 100 code points."""
    documents = [
        _quotation(
            "q1",
            validity="天" * 101,
            line_items=[
                _line(description="价" * 61, unit="件" * 11),
                {"qty": 1},
                _line(description=5, unit=["unit"]),
            ],
        )
    ]

    payload, _ = await _draft_input(async_session_maker, monkeypatch, documents)

    quotation = payload["documents"][0]
    assert quotation["validity"] == "天" * 100
    assert [(line["description"], line["unit"]) for line in quotation["lines"]] == [
        ("价" * 60, "件" * 10),
        ("", None),
        ("", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "value", "target", "expected"),
    [
        ("qty", 2, "qty", "2"),
        ("qty", "2.000", "qty", "2"),
        ("qty", "1.50", "qty", "1.5"),
        ("qty", "abc", "qty", None),
        ("qty", None, "qty", None),
        ("qty", True, "qty", None),
        ("unit_price", 4300.0, "unit_price", "4300.00"),
        ("unit_price", "abc", "unit_price", None),
        ("unit_price", None, "unit_price", None),
        ("unit_price", True, "unit_price", None),
        ("discount_amt", "12.345", "discount", "12.35"),
        ("sub_total", 7, "sub_total", "7.00"),
    ],
)
async def test_line_numbers_are_normalized(
    async_session_maker, monkeypatch, source, value, target, expected
):
    """Guards criterion 2 (numbers): qty without trailing zeros, money as two-decimal strings,
    and non-numeric, null, or boolean values as null."""
    documents = [_quotation("q1", line_items=[_line(**{source: value})])]

    payload, _ = await _draft_input(async_session_maker, monkeypatch, documents)

    assert payload["documents"][0]["lines"][0][target] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "instruction", "schema"),
    [
        ("whatsapp", None, WHATSAPP_DRAFT_SCHEMA),
        ("email", "  Be brief  ", EMAIL_DRAFT_SCHEMA),
    ],
)
async def test_draft_request_has_exact_keys_and_prompt(
    async_session_maker, monkeypatch, channel, instruction, schema
):
    """Guards criteria 4 and 5 (request): exact payload keys with and without instruction,
    empty documents without truncation, unchanged schema, and the section-four prompt rules."""
    payload, call = await _draft_input(
        async_session_maker, monkeypatch, channel=channel, instruction=instruction
    )

    expected_keys = {
        "as_of",
        "output_language",
        "snapshot",
        "documents",
        "documents_truncated",
        "channel",
    }
    if instruction is not None:
        expected_keys.add("instruction")
    assert set(payload) == expected_keys
    assert payload["documents"] == [] and payload["documents_truncated"] is False
    assert call["schema"] == schema
    assert call["system"] == REPLY_DRAFT_SYSTEM
    for phrase in ("documents", "synced_at", "validity", "documents_truncated", "To confirm"):
        assert phrase in call["system"]


def test_prompt_forbids_unpromised_commitments_and_fixes_placeholder_form():
    """Guards CRM-TASK-081 criterion 1: the two sentences appear once each, right after the
    placeholder rule and before the documents_truncated rule."""
    system = " ".join(REPLY_DRAFT_SYSTEM.split())
    commitment = (
        "Never state that the company can do something for the customer, such as deliver to a "
        "place, have an item in stock, or meet a date, unless an agent or AI reply in the "
        "snapshot or a document already says so; otherwise write it as a placeholder."
    )
    placeholder = (
        "Write each placeholder inside its own sentence and name only what is unknown; never use "
        "a placeholder in place of a fact given in the snapshot or documents, and do not add "
        "another sentence saying you will confirm it or get back to the customer."
    )

    assert system.count(commitment) == 1
    assert system.count(placeholder) == 1
    assert (
        system.index("explain it in note_for_agent")
        < system.index(commitment)
        < system.index("When documents_truncated is true")
    )


@pytest.mark.asyncio
async def test_brief_request_has_no_documents(async_session_maker, monkeypatch):
    """Guards criterion 4 (unchanged): the conversation brief payload has no documents."""
    await _seed(async_session_maker, [_quotation("q1", line_items=[_line()])])
    calls = []
    _client(
        monkeypatch,
        calls,
        {
            "customer_need": "Need",
            "conversation_state": "Open",
            "customer_mood": "calm",
            "open_questions": [],
            "suggested_next_step": "Reply",
        },
    )
    async with async_session_maker() as session:
        result = await ai_assist_service.generate_conversation_brief(
            session, user_id="user-1", contact_id="contact-1", output_language="en"
        )

    assert result.category == "ok"
    payload = json.loads(calls[0]["messages"][0]["content"])
    assert set(payload) == {"as_of", "output_language", "snapshot"}


@pytest.mark.asyncio
async def test_documents_without_messages_stay_no_messages(async_session_maker, monkeypatch):
    """Guards criterion 4 (unchanged): documents alone still give no_messages, no call, no audit."""
    await _seed(async_session_maker, [_document("iv-1")], with_message=False)
    calls = []
    _client(monkeypatch, calls)
    async with async_session_maker() as session:
        result = await ai_assist_service.generate_reply_draft(
            session,
            user_id="user-1",
            contact_id="contact-1",
            channel="whatsapp",
            output_language="en",
        )
        await session.commit()
        audits = (await session.execute(select(AiRequest))).scalars().all()

    assert result.category == "no_messages"
    assert calls == [] and audits == []
