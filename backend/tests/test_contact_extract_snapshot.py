"""CRM-TASK-050 acceptance tests for contact extract snapshots."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.contact import Contact
from app.models.message import Message
from app.services import contact_extract_service
from app.services.ai_api_client import compact_json
from app.services.contact_extract_service import (
    EXTRACT_FIELDS,
    INDUSTRIES,
    build_extract_snapshot,
)

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
BASE = datetime(2026, 10, 1, 8, 0)


def _contact(**values):
    fields = {"id": "contact-id-7f3a", "name": "Tan"}
    fields.update(values)
    return Contact(**fields)


def _message(message_id, **values):
    fields = {
        "id": message_id,
        "contact_id": "contact-id-7f3a",
        "channel": "whatsapp",
        "direction": "inbound",
        "author_kind": "customer",
        "sender_id": "sender",
        "recipient_id": "recipient",
        "body": message_id,
        "created_at": BASE,
    }
    fields.update(values)
    return Message(**fields)


async def _snapshot(maker, contact, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()
        return await build_extract_snapshot(session, contact=contact, now=NOW)


@pytest.mark.asyncio
async def test_snapshot_keys_and_no_existing_values(async_session_maker):
    """Guards criteria 1 and 2: exact top-level and contact keys, no field values or ids."""
    contact = _contact(
        company="Acme Widgets",
        industry="房地产",
        email="tan@example.com",
        phone="60198887777",
        address="Jalan Kebun 12",
        assigned_to="owner-id-9c2b",
    )
    snapshot = await _snapshot(async_session_maker, contact, _message("m1", body="hello"))
    assert set(snapshot) == {"contact", "messages", "truncated"}
    assert set(snapshot["contact"]) == {"name", "name_is_phone", "empty_fields"}
    assert snapshot["contact"]["name"] == "Tan"
    assert snapshot["contact"]["empty_fields"] == []
    text = compact_json(snapshot)
    for value in (
        "Acme Widgets",
        "房地产",
        "tan@example.com",
        "60198887777",
        "Jalan Kebun 12",
        "contact-id-7f3a",
        "owner-id-9c2b",
        "m1",
    ):
        assert value not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("60123456789", True),
        ("+60 12-345 6789", True),
        ("Tan", False),
        ("", False),
        ("Tan 0123", False),
    ],
)
async def test_name_is_phone(async_session_maker, name, expected):
    """Guards criterion 2: name_is_phone only for digit, plus, space and dash names."""
    snapshot = await _snapshot(async_session_maker, _contact(name=name))
    assert snapshot["contact"]["name_is_phone"] is expected
    assert snapshot["contact"]["name"] == name


@pytest.mark.asyncio
async def test_empty_fields_rules_and_order(async_session_maker):
    """Guards criterion 2: null, empty and whitespace-only count as empty, in fixed order."""
    assert EXTRACT_FIELDS == ("company", "industry", "email", "phone", "address")
    contact = _contact(company=None, industry="零售/电商", email="", phone="0123", address=" \t\n ")
    snapshot = await _snapshot(async_session_maker, contact)
    assert snapshot["contact"]["empty_fields"] == ["company", "email", "address"]

    contact = _contact(address="", phone=None, email="  ", industry=None, company="")
    snapshot = await _snapshot(async_session_maker, contact)
    assert snapshot["contact"]["empty_fields"] == list(EXTRACT_FIELDS)


@pytest.mark.asyncio
async def test_messages_latest_forty_ordering_and_index(async_session_maker):
    """Guards criteria 3 and 4: latest 40 old-to-new, id tie-break, own contact, over-40 flag."""
    rows = [
        _message(f"m{index:02}", created_at=BASE + timedelta(seconds=index)) for index in range(39)
    ]
    rows += [
        _message("tie-b", created_at=BASE + timedelta(seconds=100)),
        _message("tie-a", created_at=BASE + timedelta(seconds=100)),
        _message(
            "other",
            contact_id="someone-else",
            created_at=BASE + timedelta(seconds=200),
        ),
    ]
    snapshot = await _snapshot(async_session_maker, _contact(), *rows)
    assert [item["body"] for item in snapshot["messages"]] == [
        *[f"m{index:02}" for index in range(1, 39)],
        "tie-a",
        "tie-b",
    ]
    assert [item["index"] for item in snapshot["messages"]] == list(range(40))
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_message_item_shapes_inference_and_body_truncation(async_session_maker):
    """Guards criteria 3 and 4: item keys, at format, author_kind inference, subject, body cap."""
    rows = [
        _message("inbound", author_kind=None, created_at=BASE + timedelta(microseconds=500)),
        _message(
            "outbound",
            direction="outbound",
            author_kind=None,
            created_at=BASE + timedelta(seconds=1),
        ),
        _message(
            "ai", direction="outbound", author_kind="ai", created_at=BASE + timedelta(seconds=2)
        ),
        _message(
            "email",
            channel="email",
            subject="Quote",
            created_at=BASE + timedelta(seconds=3),
        ),
        _message(
            "email-none",
            channel="email",
            subject=None,
            created_at=BASE + timedelta(seconds=4),
        ),
    ]
    snapshot = await _snapshot(async_session_maker, _contact(), *rows)
    messages = snapshot["messages"]
    assert set(messages[0]) == {"index", "at", "direction", "author_kind", "channel", "body"}
    assert messages[0]["at"] == "2026-10-01T08:00:00Z"
    assert [item["author_kind"] for item in messages[:3]] == ["customer", "agent", "ai"]
    assert set(messages[3]) == {
        "index",
        "at",
        "direction",
        "author_kind",
        "channel",
        "body",
        "subject",
    }
    assert messages[3]["subject"] == "Quote"
    assert messages[4]["subject"] == ""
    assert snapshot["truncated"] is False

    async with async_session_maker() as session:
        session.add(_message("long", body="x" * 1001, created_at=BASE + timedelta(seconds=5)))
        await session.commit()
        snapshot = await build_extract_snapshot(session, contact=_contact(), now=NOW)
    assert snapshot["messages"][-1]["body"] == "x" * 1000
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_size_limit_drops_oldest_and_reindexes(async_session_maker):
    """Guards criterion 4: over 16 KiB drops oldest messages, reindexes, sets truncated."""
    rows = [
        _message(
            f"m{index:02}",
            body=f"{index:02}:" + "测" * 997,
            created_at=BASE + timedelta(seconds=index),
        )
        for index in range(40)
    ]
    snapshot = await _snapshot(async_session_maker, _contact(), *rows)
    remaining = [int(item["body"].split(":", 1)[0]) for item in snapshot["messages"]]
    assert 1 <= len(remaining) < 40
    assert remaining == list(range(40 - len(remaining), 40))
    assert [item["index"] for item in snapshot["messages"]] == list(range(len(remaining)))
    assert len(compact_json(snapshot).encode("utf-8")) <= 16384
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_size_limit_keeps_one_message_when_still_oversized(async_session_maker, monkeypatch):
    """Guards criterion 4: at least one message stays even when still over the limit."""
    assert contact_extract_service.SNAPSHOT_LIMIT_BYTES == 16384
    monkeypatch.setattr(contact_extract_service, "SNAPSHOT_LIMIT_BYTES", 10)
    rows = [_message(f"m{index}", created_at=BASE + timedelta(seconds=index)) for index in range(3)]
    snapshot = await _snapshot(async_session_maker, _contact(), *rows)
    assert [(item["index"], item["body"]) for item in snapshot["messages"]] == [(0, "m2")]
    assert len(compact_json(snapshot).encode("utf-8")) > 10
    assert snapshot["truncated"] is True


@pytest.mark.asyncio
async def test_small_snapshot_is_not_truncated(async_session_maker):
    """Guards criterion 4: a small snapshot with nothing cut keeps truncated false."""
    rows = [
        _message("m0", body="a" * 1000),
        _message("m1", channel="email", subject="s" * 100, created_at=BASE + timedelta(1)),
    ]
    snapshot = await _snapshot(async_session_maker, _contact(name="n" * 100), *rows)
    assert len(snapshot["messages"]) == 2
    assert snapshot["truncated"] is False


def test_industries_match_frontend():
    """Guards criterion 1: INDUSTRIES equals the ten ContactForm industries in order."""
    assert list(INDUSTRIES) == [
        "科技/IT",
        "金融/保险",
        "医疗/健康",
        "教育/培训",
        "零售/电商",
        "制造/工业",
        "房地产",
        "咨询/服务",
        "餐饮/酒店",
        "物流/供应链",
    ]
