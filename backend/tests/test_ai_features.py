"""CRM-TASK-023 tests for prompt definitions and AI-assistance business rules."""

import json

import pytest
from sqlalchemy import select

from app.models.ai_request import AiRequest
from app.models.contact import Contact
from app.models.message import Message
from app.services import ai_api_client, ai_assist_service
from app.services.ai_features import (
    BRIEF_FEATURE,
    BRIEF_SCHEMA,
    EMAIL_DRAFT_SCHEMA,
    MAX_TOKENS,
    REPLY_DRAFT_FEATURE,
    TRANSLATE_FEATURE,
    TRANSLATE_SCHEMA,
    WHATSAPP_DRAFT_SCHEMA,
    validate_brief,
    validate_reply_draft,
    validate_translation,
)


async def _seed(maker, with_message=True):
    async with maker() as session:
        session.add(Contact(id="contact-1", name="Name"))
        if with_message:
            session.add(
                Message(
                    id="message-1",
                    contact_id="contact-1",
                    channel="whatsapp",
                    direction="inbound",
                    sender_id="sender",
                    recipient_id="recipient",
                    body="Need help",
                )
            )
        await session.commit()


def _configured(monkeypatch):
    monkeypatch.setattr(ai_assist_service.ai_api_client, "is_ai_configured", lambda: True)


def _client(monkeypatch, result, calls):
    async def send_message(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(ai_assist_service.ai_api_client, "send_message", send_message)


@pytest.mark.asyncio
async def test_each_feature_sends_its_contract_and_audits_success(async_session_maker, monkeypatch):
    """Guards the feature, prompt, schema, max-token, JSON-input, and audit acceptance criteria."""
    await _seed(async_session_maker)
    _configured(monkeypatch)
    calls = []
    results = iter(
        [
            ai_api_client.AiApiResult(
                "ok",
                {
                    "customer_need": "Need",
                    "conversation_state": "Open",
                    "customer_mood": "calm",
                    "open_questions": [],
                    "suggested_next_step": "Reply",
                },
                "brief-1",
            ),
            ai_api_client.AiApiResult("ok", {"draft": "Reply", "note_for_agent": ""}, "draft-1"),
            ai_api_client.AiApiResult(
                "ok", {"translation": "Hello", "detected_language": "en"}, "translate-1"
            ),
        ]
    )

    async def send_message(**kwargs):
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(ai_assist_service.ai_api_client, "send_message", send_message)
    async with async_session_maker() as session:
        brief = await ai_assist_service.generate_conversation_brief(
            session, user_id="user-1", contact_id="contact-1", output_language="en"
        )
        draft = await ai_assist_service.generate_reply_draft(
            session,
            user_id="user-1",
            contact_id="contact-1",
            channel="whatsapp",
            output_language="en",
            instruction="  Be friendly  ",
        )
        translation = await ai_assist_service.translate_text(
            session,
            user_id="user-1",
            contact_id="contact-1",
            text="  你好  ",
            target_language="en",
        )
        await session.commit()
        audits = (
            (await session.execute(select(AiRequest).order_by(AiRequest.feature))).scalars().all()
        )

    assert (brief.category, draft.category, translation.category) == ("ok", "ok", "ok")
    assert [(item.feature, item.outcome) for item in audits] == [
        (BRIEF_FEATURE, "ok"),
        (REPLY_DRAFT_FEATURE, "ok"),
        (TRANSLATE_FEATURE, "ok"),
    ]
    assert [call["feature"] for call in calls] == [
        BRIEF_FEATURE,
        REPLY_DRAFT_FEATURE,
        TRANSLATE_FEATURE,
    ]
    assert all(call["max_tokens"] == MAX_TOKENS for call in calls)
    assert [call["schema"] for call in calls] == [
        BRIEF_SCHEMA,
        WHATSAPP_DRAFT_SCHEMA,
        TRANSLATE_SCHEMA,
    ]
    assert all("untrusted data" in call["system"] for call in calls)
    assert "[To confirm:" in calls[1]["system"]
    assert "faithfully" in calls[2]["system"]
    assert json.loads(calls[0]["messages"][0]["content"])["snapshot"]["messages"]
    draft_input = json.loads(calls[1]["messages"][0]["content"])
    assert draft_input["instruction"] == "Be friendly" and draft_input["channel"] == "whatsapp"
    assert json.loads(calls[2]["messages"][0]["content"]) == {
        "text": "你好",
        "target_language": "en",
    }


@pytest.mark.parametrize(
    ("output", "validator"),
    [
        (
            {
                "customer_need": "",
                "conversation_state": "s",
                "customer_mood": "calm",
                "open_questions": [],
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n" * 201,
                "conversation_state": "s",
                "customer_mood": "calm",
                "open_questions": [],
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n",
                "conversation_state": "",
                "customer_mood": "calm",
                "open_questions": [],
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n",
                "conversation_state": "s" * 301,
                "customer_mood": "calm",
                "open_questions": [],
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n",
                "conversation_state": "s",
                "customer_mood": "bad",
                "open_questions": [],
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n",
                "conversation_state": "s",
                "customer_mood": "calm",
                "open_questions": ["a"] * 4,
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n",
                "conversation_state": "s",
                "customer_mood": "calm",
                "open_questions": [""],
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n",
                "conversation_state": "s",
                "customer_mood": "calm",
                "open_questions": ["q" * 121],
                "suggested_next_step": "",
            },
            validate_brief,
        ),
        (
            {
                "customer_need": "n",
                "conversation_state": "s",
                "customer_mood": "calm",
                "open_questions": [],
                "suggested_next_step": "s" * 241,
            },
            validate_brief,
        ),
        (
            {"draft": "x" * 1001, "note_for_agent": ""},
            lambda value: validate_reply_draft(value, "whatsapp"),
        ),
        (
            {"draft": "x" * 1501, "subject": "s", "note_for_agent": ""},
            lambda value: validate_reply_draft(value, "email"),
        ),
        (
            {"draft": "d", "subject": "", "note_for_agent": ""},
            lambda value: validate_reply_draft(value, "email"),
        ),
        (
            {"draft": "d", "subject": "s" * 151, "note_for_agent": ""},
            lambda value: validate_reply_draft(value, "email"),
        ),
        (
            {"draft": "d", "subject": "s", "note_for_agent": "x" * 201},
            lambda value: validate_reply_draft(value, "email"),
        ),
        ({"translation": "", "detected_language": "en"}, validate_translation),
        ({"translation": "t" * 8001, "detected_language": "en"}, validate_translation),
        ({"translation": "ok", "detected_language": "fr"}, validate_translation),
    ],
)
def test_every_output_field_limit_count_and_enum_is_rejected(output, validator):
    """Guards the acceptance criterion that every output field boundary becomes an AI error."""
    assert validator(output) is None


@pytest.mark.asyncio
async def test_invalid_success_output_becomes_error_and_keeps_request_id(
    async_session_maker, monkeypatch
):
    """Guards reclassification of invalid successful output and request-id audit reconciliation."""
    await _seed(async_session_maker)
    _configured(monkeypatch)
    calls = []
    _client(
        monkeypatch,
        ai_api_client.AiApiResult("ok", {"translation": "", "detected_language": "en"}, "billed"),
        calls,
    )
    async with async_session_maker() as session:
        result = await ai_assist_service.translate_text(
            session, user_id="user-1", contact_id="contact-1", text="hello", target_language="en"
        )
        await session.commit()
        audit = (await session.execute(select(AiRequest))).scalar_one()

    assert result.category == audit.outcome == "error"
    assert result.request_id == audit.request_id == "billed"


@pytest.mark.asyncio
async def test_unavailable_is_audited_after_call(async_session_maker, monkeypatch):
    """Guards the temporary-unavailability result and its audit outcome acceptance criteria."""
    await _seed(async_session_maker)
    _configured(monkeypatch)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("unavailable"), calls)
    async with async_session_maker() as session:
        brief = await ai_assist_service.generate_conversation_brief(
            session, user_id="user-1", contact_id="contact-1", output_language="en"
        )
        draft = await ai_assist_service.generate_reply_draft(
            session,
            user_id="user-1",
            contact_id="contact-1",
            channel="email",
            output_language="en",
        )
        translation = await ai_assist_service.translate_text(
            session, user_id="user-1", contact_id="contact-1", text="hello", target_language="en"
        )
        await session.commit()
        audits = (
            (await session.execute(select(AiRequest).order_by(AiRequest.feature))).scalars().all()
        )

    assert [result.category for result in (brief, draft, translation)] == ["unavailable"] * 3
    assert [(audit.feature, audit.outcome) for audit in audits] == [
        (BRIEF_FEATURE, "unavailable"),
        (REPLY_DRAFT_FEATURE, "unavailable"),
        (TRANSLATE_FEATURE, "unavailable"),
    ]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_not_configured_invalid_input_and_no_messages_do_not_call_or_audit(
    async_session_maker, monkeypatch
):
    """Guards parameter, configuration, and no-message ordering with no call and no audit."""
    await _seed(async_session_maker, with_message=False)
    calls = []
    _client(monkeypatch, ai_api_client.AiApiResult("ok"), calls)
    monkeypatch.setattr(ai_assist_service.ai_api_client, "is_ai_configured", lambda: False)
    async with async_session_maker() as session:
        invalid = await ai_assist_service.translate_text(
            session, user_id="user-1", contact_id="contact-1", text="   ", target_language="en"
        )
        unconfigured_brief = await ai_assist_service.generate_conversation_brief(
            session, user_id="user-1", contact_id="contact-1", output_language="en"
        )
        unconfigured_draft = await ai_assist_service.generate_reply_draft(
            session,
            user_id="user-1",
            contact_id="contact-1",
            channel="email",
            output_language="en",
        )
        unconfigured_translation = await ai_assist_service.translate_text(
            session, user_id="user-1", contact_id="contact-1", text="text", target_language="en"
        )
        monkeypatch.setattr(ai_assist_service.ai_api_client, "is_ai_configured", lambda: True)
        no_messages_brief = await ai_assist_service.generate_conversation_brief(
            session, user_id="user-1", contact_id="contact-1", output_language="en"
        )
        no_messages_draft = await ai_assist_service.generate_reply_draft(
            session, user_id="user-1", contact_id="contact-1", channel="email", output_language="en"
        )
        await session.commit()
        audits = (await session.execute(select(AiRequest))).scalars().all()

    assert [
        result.category
        for result in (
            invalid,
            unconfigured_brief,
            unconfigured_draft,
            unconfigured_translation,
            no_messages_brief,
            no_messages_draft,
        )
    ] == [
        "invalid_input",
        "not_configured",
        "not_configured",
        "not_configured",
        "no_messages",
        "no_messages",
    ]
    assert calls == [] and audits == []
    assert EMAIL_DRAFT_SCHEMA["required"] == ["draft", "subject", "note_for_agent"]
