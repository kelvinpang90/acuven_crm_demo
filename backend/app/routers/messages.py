from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.schemas.message import EmailSendRequest, WhatsAppSendRequest
from app.services import access_service, chatbot_send_service, email_service, inbox_events
from app.services.email_service import EmailSendError
from app.utils.response import fail, ok

router = APIRouter()

NOT_YOURS = (
    "This customer is not assigned to you. Ask an administrator to send it, "
    "or have the customer assigned to you first."
)
NOT_YOURS_TO_READ = "This conversation is not assigned to you."


@router.get("")
async def list_messages(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    channel: Optional[str] = None,
    is_read: Optional[bool] = None,
    contact_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    query = select(Message)

    # Data permission
    if current_user.role == "sales":
        query = query.where(Message.assigned_to == current_user.id)
    elif current_user.role == "manager":
        from app.services.dashboard_service import _get_team_ids

        team_ids = await _get_team_ids(db, current_user.id)
        query = query.where(Message.assigned_to.in_(team_ids))

    if channel:
        query = query.where(Message.channel == channel)
    if is_read is not None:
        query = query.where(Message.is_read == is_read)
    if contact_id:
        query = query.where(Message.contact_id == contact_id)

    # Count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Paginate
    query = query.order_by(Message.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    messages = result.scalars().all()

    items = await _message_dicts(db, messages, include_contact_name=True)

    return ok(
        data={
            "data": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/contact/{contact_id}")
async def contact_messages(
    contact_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not await access_service.may_access_contact(db, current_user, contact_id):
        return fail(message=NOT_YOURS_TO_READ, code="NOT_ASSIGNED", status_code=403)

    result = await db.execute(
        select(Message).where(Message.contact_id == contact_id).order_by(Message.created_at.asc())
    )
    messages = await _message_dicts(db, result.scalars().all())
    return ok(data=messages)


@router.get("/stream")
async def message_stream(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Server-sent events: `messages_changed` whenever a message write commits."""
    # The stream outlives this handler. FastAPI 0.111 closes get_db's session before
    # streaming, but newer releases keep it until the response ends, which would pin
    # a pooled connection per open stream; release it here either way.
    await db.close()
    return StreamingResponse(
        inbox_events.stream(),
        media_type="text/event-stream",
        # X-Accel-Buffering: nginx must pass each event through, not buffer it.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/{message_id}/read")
async def mark_read(
    message_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Message).where(Message.id == message_id))
    msg = result.scalar_one_or_none()
    # Judged by Message.assigned_to, the column the inbox list filters on. A
    # missing message has no owner, so only an admin gets as far as "not found".
    if not await access_service.may_access_owner(
        db, current_user, msg.assigned_to if msg else None
    ):
        return fail(message=NOT_YOURS_TO_READ, code="NOT_ASSIGNED", status_code=403)
    if not msg:
        return fail(message="Message not found", code=404)
    msg.is_read = True
    await db.commit()
    return ok(data=(await _message_dicts(db, [msg]))[0])


@router.post("/whatsapp/send")
async def send_whatsapp(
    body: WhatsAppSendRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not await access_service.may_access_contact(db, current_user, body.contact_id):
        return fail(message=NOT_YOURS, code="NOT_ASSIGNED", status_code=403)

    result = await chatbot_send_service.send_agent_reply(
        db,
        contact_id=body.contact_id,
        text=body.message,
        agent_id=current_user.id,
        agent_name=current_user.name,
    )
    return await _send_result(db, result)


@router.post("/{message_id}/whatsapp/retry")
async def retry_whatsapp(
    message_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    message = (
        await db.execute(select(Message).where(Message.id == message_id))
    ).scalar_one_or_none()
    contact_is_live = (
        message is not None
        and (
            await db.execute(
                select(Contact.id).where(
                    Contact.id == message.contact_id, Contact.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
    )
    if (
        message is None
        or message.author_kind != "agent"
        or not await access_service.may_access_contact(db, current_user, message.contact_id)
        or contact_is_live is None
    ):
        return fail(message=NOT_YOURS, code="NOT_ASSIGNED", status_code=403)
    result = await chatbot_send_service.retry_agent_reply(db, message_id=message_id)
    if result.category in (chatbot_send_service.NOT_FOUND, chatbot_send_service.NOT_RETRYABLE):
        return fail(message=NOT_YOURS, code="NOT_ASSIGNED", status_code=403)
    return await _send_result(db, result)


@router.post("/email/send")
async def send_email(
    body: EmailSendRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not await access_service.may_access_contact(db, current_user, body.contact_id):
        return fail(message=NOT_YOURS, code="NOT_ASSIGNED", status_code=403)

    try:
        data = await email_service.send_email(db, body.contact_id, body.subject, body.body)
    except EmailSendError as exc:
        if exc.reason == "not_configured":
            return fail(
                message="Email (SMTP) is not configured; the email was not sent",
                code="NOT_CONFIGURED",
                status_code=503,
            )
        return fail(message=f"Email send error: {exc.detail}", code="API_ERROR", status_code=502)
    if not data:
        return fail(message="Contact not found or has no email", code=400)
    return ok(data=data)


def _msg_to_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "contact_id": m.contact_id,
        "channel": m.channel,
        "direction": m.direction,
        "sender_id": m.sender_id,
        "recipient_id": m.recipient_id,
        "subject": m.subject,
        "body": m.body,
        "external_id": m.external_id,
        "is_read": m.is_read,
        "assigned_to": m.assigned_to,
        "author_kind": m.author_kind,
        "bot_id": m.bot_id,
        "delivery_status": m.delivery_status,
        "delivery_error": m.delivery_error,
        # See whatsapp_service._msg_to_dict for rationale.
        "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
        "contact_name": None,
        "sent_by": m.sent_by,
        "sent_by_name": None,
    }


async def _message_dicts(
    db: AsyncSession, messages: list[Message], *, include_contact_name: bool = False
) -> list[dict]:
    user_ids = {m.sent_by for m in messages if m.sent_by}
    user_names = {}
    if user_ids:
        user_names = dict(
            (await db.execute(select(User.id, User.name).where(User.id.in_(user_ids)))).all()
        )
    contact_names = {}
    if include_contact_name:
        contact_ids = {m.contact_id for m in messages if m.contact_id}
        if contact_ids:
            contact_names = dict(
                (
                    await db.execute(
                        select(Contact.id, Contact.name).where(Contact.id.in_(contact_ids))
                    )
                ).all()
            )
    items = []
    for message in messages:
        item = _msg_to_dict(message)
        item["sent_by_name"] = user_names.get(message.sent_by)
        if include_contact_name:
            item["contact_name"] = contact_names.get(message.contact_id)
        items.append(item)
    return items


async def _send_result(db: AsyncSession, result: chatbot_send_service.SendReplyResult):
    category = result.category
    if category == chatbot_send_service.SENT:
        return ok(data=(await _message_dicts(db, [result.message]))[0])
    if category == chatbot_send_service.UNCONFIRMED:
        return ok(
            data=(await _message_dicts(db, [result.message]))[0],
            message="UNCONFIRMED",
            status_code=202,
        )
    if category == chatbot_send_service.NO_PHONE:
        return fail("Contact not found or has no phone number", code="NO_PHONE", status_code=400)
    if category == chatbot_send_service.WINDOW_CLOSED:
        return fail(
            "WhatsApp reply window is closed",
            code="WINDOW_CLOSED",
            fields={"window_closed_at": result.window_until},
            status_code=409,
        )
    if category == chatbot_send_service.NOT_CONFIGURED:
        return fail("Chatbot is not configured", code="NOT_CONFIGURED", status_code=503)
    if category == chatbot_send_service.IN_PROGRESS:
        return fail("Message is still being sent", code="IN_PROGRESS", status_code=409)
    if category == chatbot_send_service.RETRY_EXPIRED:
        return fail("Retry window has expired", code="RETRY_EXPIRED", status_code=409)
    if category == chatbot_send_service.SEND_FAILED:
        return fail(
            "Chatbot did not send the message",
            code="SEND_FAILED",
            fields={"error_code": result.error_code, "error_title": result.error_title},
            status_code=502,
        )
    return fail(message=NOT_YOURS, code="NOT_ASSIGNED", status_code=403)
