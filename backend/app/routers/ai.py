from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.user import User
from app.schemas.ai import (
    ContactExtractRequest,
    ContactSummaryRequest,
    ConversationBriefRequest,
    DashboardInsightRequest,
    DealNextStepRequest,
    FollowupDraftRequest,
    ReplyDraftRequest,
    TranslateRequest,
)
from app.services import (
    access_service,
    ai_api_client,
    ai_assist_service,
    contact_extract_service,
    contact_summary_service,
    dashboard_insight_service,
    deal_next_step_service,
    followup_draft_service,
)
from app.utils.response import fail, ok

router = APIRouter()

NOT_ASSIGNED_MESSAGE = "This customer is not assigned to you."


def _default_output_language(user: User) -> str:
    return "zh-CN" if user.language == "zh" else "en"


async def _may_use_ai_for_contact(db: AsyncSession, user: User, contact_id: str) -> bool:
    contact_exists = (
        await db.execute(
            select(Contact.id).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    return contact_exists is not None and await access_service.may_access_contact(
        db, user, contact_id
    )


async def _may_use_ai_for_deal(db: AsyncSession, user: User, deal_id: str) -> bool:
    deal = (
        await db.execute(select(Deal).where(Deal.id == deal_id, Deal.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if deal is None or not await access_service.may_access_deal(db, user, deal_id):
        return False
    contact_exists = (
        await db.execute(
            select(Contact.id).where(Contact.id == deal.contact_id, Contact.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    return contact_exists is not None and await access_service.may_access_contact(
        db, user, deal.contact_id
    )


def _not_assigned():
    return fail(NOT_ASSIGNED_MESSAGE, code="NOT_ASSIGNED", status_code=403)


def _result_response(result: ai_assist_service.AiAssistResult, fields: tuple[str, ...]):
    if result.category == ai_assist_service.OK:
        return ok(data={field: getattr(result, field) for field in fields})
    errors = {
        ai_assist_service.NOT_CONFIGURED: (503, "AI_NOT_CONFIGURED", "AI is not configured."),
        ai_assist_service.UNAVAILABLE: (503, "AI_UNAVAILABLE", "AI is temporarily unavailable."),
        ai_assist_service.ERROR: (502, "AI_ERROR", "AI request failed."),
        ai_assist_service.NO_MESSAGES: (400, "NO_MESSAGES", "Conversation has no messages."),
        ai_assist_service.INVALID_INPUT: (422, "INVALID_INPUT", "Invalid AI request."),
    }
    status_code, code, message = errors[result.category]
    return fail(message, code=code, status_code=status_code)


@router.post("/conversations/{contact_id}/brief")
async def create_conversation_brief(
    contact_id: str,
    body: ConversationBriefRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not await _may_use_ai_for_contact(db, current_user, contact_id):
        return _not_assigned()
    result = await ai_assist_service.generate_conversation_brief(
        db,
        user_id=current_user.id,
        contact_id=contact_id,
        output_language=body.output_language or _default_output_language(current_user),
    )
    return _result_response(
        result,
        (
            "request_id",
            "customer_need",
            "conversation_state",
            "customer_mood",
            "open_questions",
            "suggested_next_step",
            "truncated",
        ),
    )


@router.post("/contacts/{contact_id}/summary")
async def create_contact_summary(
    contact_id: str,
    body: ContactSummaryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not await _may_use_ai_for_contact(db, current_user, contact_id):
        return _not_assigned()
    result = await contact_summary_service.generate_contact_summary(
        db,
        user=current_user,
        contact_id=contact_id,
        output_language=body.output_language or _default_output_language(current_user),
    )
    if result.category == ai_assist_service.NO_HISTORY:
        return fail("Contact has no history.", code="NO_HISTORY", status_code=400)
    return _result_response(result, ("request_id", "summary", "suggested_next_step", "truncated"))


@router.post("/contacts/{contact_id}/extract")
async def create_contact_extract(
    contact_id: str,
    body: ContactExtractRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not await _may_use_ai_for_contact(db, current_user, contact_id):
        return _not_assigned()
    result = await contact_extract_service.generate_contact_extract(
        db,
        user=current_user,
        contact_id=contact_id,
        output_language=body.output_language or _default_output_language(current_user),
    )
    if result.category == ai_assist_service.NOTHING_TO_FILL:
        return fail("Contact has nothing to fill.", code="NOTHING_TO_FILL", status_code=400)
    return _result_response(result, ("request_id", "suggestions", "truncated"))


@router.post("/deals/{deal_id}/next-step")
async def create_deal_next_step(
    deal_id: str,
    body: DealNextStepRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not await _may_use_ai_for_deal(db, current_user, deal_id):
        return _not_assigned()
    result = await deal_next_step_service.generate_deal_next_step(
        db,
        user=current_user,
        deal_id=deal_id,
        output_language=body.output_language or _default_output_language(current_user),
    )
    if result.category == ai_assist_service.DEAL_CLOSED:
        return fail("Deal is closed.", code="DEAL_CLOSED", status_code=400)
    if result.category == ai_assist_service.NO_HISTORY:
        return fail("Deal has no history.", code="NO_HISTORY", status_code=400)
    return _result_response(
        result,
        (
            "request_id",
            "assessment",
            "stall_reason",
            "next_steps",
            "idle_days",
            "days_in_status",
            "stalled",
            "truncated",
        ),
    )


@router.post("/contacts/{contact_id}/followup-draft")
async def create_followup_draft(
    contact_id: str,
    body: FollowupDraftRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not await _may_use_ai_for_contact(db, current_user, contact_id):
        return _not_assigned()
    result = await followup_draft_service.generate_followup_draft(
        db,
        user=current_user,
        contact_id=contact_id,
        deal_id=body.deal_id,
        note=body.note,
        today=body.today,
        output_language=body.output_language or _default_output_language(current_user),
    )
    return _result_response(result, ("request_id", "activity_type", "content", "tasks"))


@router.post("/conversations/{contact_id}/reply-draft")
async def create_reply_draft(
    contact_id: str,
    body: ReplyDraftRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not await _may_use_ai_for_contact(db, current_user, contact_id):
        return _not_assigned()
    result = await ai_assist_service.generate_reply_draft(
        db,
        user_id=current_user.id,
        contact_id=contact_id,
        channel=body.channel,
        instruction=body.instruction,
        output_language=body.output_language or _default_output_language(current_user),
    )
    return _result_response(result, ("request_id", "draft", "subject", "note_for_agent"))


@router.post("/dashboard/insight")
async def create_dashboard_insight(
    body: DashboardInsightRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role("manager"))],
):
    result = await dashboard_insight_service.generate_dashboard_insight(
        db,
        user=current_user,
        output_language=body.output_language or _default_output_language(current_user),
    )
    if result.category == ai_assist_service.NO_DATA:
        return fail("Team has no dashboard data.", code="NO_DATA", status_code=400)
    return _result_response(
        result, ("request_id", "headline", "observations", "suggestions", "truncated")
    )


@router.post("/translate")
async def translate(
    body: TranslateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not await _may_use_ai_for_contact(db, current_user, body.contact_id):
        return _not_assigned()
    result = await ai_assist_service.translate_text(
        db,
        user_id=current_user.id,
        contact_id=body.contact_id,
        text=body.text,
        target_language=body.target_language,
    )
    return _result_response(result, ("request_id", "translation", "detected_language"))


@router.get("/status")
async def ai_status(current_user: Annotated[User, Depends(get_current_user)]):
    return ok(data={"enabled": ai_api_client.is_ai_configured()})
