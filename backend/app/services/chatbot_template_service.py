"""CRM business rules for WhatsApp template messages sent through ai_chatbot_demo.

docs/chatbot-phase3.md sections 5.3, 7 and 15. Like chatbot_send_service this
has no HTTP or request-model concerns from the CRM API: the API layer checks
ownership and the parameter characters before calling it. Signing, the result
classification and retry are shared with text sends in chatbot_send_service.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.message import Message
from app.services import chatbot_event_service
from app.services.chatbot_send_service import (
    CHATBOT_FAILED,
    CHATBOT_PARTY,
    NO_PHONE,
    NOT_CONFIGURED,
    NOT_FOUND,
    SendReplyResult,
    _claimed_by_other,
    _deliver_stored,
    _last_inbound,
    _live_contact,
    _post_chatbot,
    _take_claim,
)
from app.utils.phone import normalize_phone

OK = "ok"
NO_OPT_IN = "no_opt_in"
TEMPLATE_PENDING = "template_pending"
TEMPLATE_UNKNOWN = "template_unknown"
TEMPLATE_PARAMS = "template_params"

PLACEHOLDER = re.compile(r"\{\{(\d+)\}\}")

logger = logging.getLogger(__name__)


@dataclass
class TemplateListResult:
    category: str
    templates: list[dict[str, Any]] = field(default_factory=list)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _valid_param(param: Any) -> bool:
    if not isinstance(param, dict) or not isinstance(param.get("label"), str):
        return False
    max_len = param.get("max_len")
    return isinstance(max_len, int) and not isinstance(max_len, bool) and max_len > 0


def _valid_template(item: Any) -> bool:
    """Design section 15 item 10: body placeholders must be exactly {{1}}..{{n}}."""
    if not isinstance(item, dict):
        return False
    if not all(_non_empty_str(item.get(key)) for key in ("name", "language", "body")):
        return False
    params = item.get("params")
    if not isinstance(params, list) or not all(_valid_param(param) for param in params):
        return False
    numbers = {int(number) for number in PLACEHOLDER.findall(item["body"])}
    return numbers == set(range(1, len(params) + 1))


async def list_templates() -> TemplateListResult:
    """Read chatbot's whitelist for CRM; not cached (design section 7)."""
    if not (settings.chatbot_send_url and settings.chatbot_send_secret):
        return TemplateListResult(NOT_CONFIGURED)
    response, payload, _ = await _post_chatbot("/internal/crm/templates", {})
    templates = payload.get("templates")
    if response is None or response.status_code != 200 or not isinstance(templates, list):
        return TemplateListResult(CHATBOT_FAILED)
    valid = []
    for item in templates:
        if _valid_template(item):
            valid.append(item)
        else:
            name = item.get("name") if isinstance(item, dict) else None
            logger.warning("dropping invalid chatbot template", extra={"template": name})
    return TemplateListResult(OK, templates=valid)


async def _template_pending(db: AsyncSession, contact_id: str) -> bool:
    """Design section 15 items 5 and 10: a template exists and no WhatsApp
    inbound is strictly later than the newest one."""
    latest_template = (
        await db.execute(
            select(func.max(Message.created_at)).where(
                Message.contact_id == contact_id,
                Message.author_kind == "agent",
                Message.template_name.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if latest_template is None:
        return False
    latest_inbound = await _last_inbound(db, contact_id)
    return latest_inbound is None or not latest_inbound > latest_template


def _clean_params(template: dict[str, Any], params: list[Any]) -> Optional[list[str]]:
    specs = template["params"]
    if len(params) != len(specs):
        return None
    cleaned = []
    for value, spec in zip(params, specs):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if value == "" or len(value) > spec["max_len"]:
            return None
        cleaned.append(value)
    return cleaned


def _render(body: str, params: list[str]) -> str:
    return PLACEHOLDER.sub(lambda match: params[int(match.group(1)) - 1], body)


async def send_template(
    db: AsyncSession,
    *,
    contact_id: str,
    name: str,
    language: str,
    params: list[str],
    agent_id: str,
    agent_name: str,
    now: Optional[datetime] = None,
) -> SendReplyResult:
    """Check in the order of design section 15 item 8, persist, then send one template."""
    contact = await _live_contact(db, contact_id)
    if contact is None:
        return SendReplyResult(NOT_FOUND)

    current = chatbot_event_service.to_naive_utc(now) if now else datetime.utcnow()
    claimed = _claimed_by_other(contact, agent_id, current)
    if claimed is not None:
        return claimed

    phone = normalize_phone(contact.phone)
    if phone is None:
        return SendReplyResult(NO_PHONE)

    if await _last_inbound(db, contact.id) is None:
        return SendReplyResult(NO_OPT_IN)
    if await _template_pending(db, contact.id):
        return SendReplyResult(TEMPLATE_PENDING)

    if not (settings.chatbot_send_url and settings.chatbot_send_secret):
        return SendReplyResult(NOT_CONFIGURED)

    # End the read transaction so the whitelist call holds no snapshot, then
    # re-read the contact the message and claim are written against.
    await db.commit()
    listed = await list_templates()
    if listed.category != OK:
        return SendReplyResult(listed.category)
    template = next(
        (
            item
            for item in listed.templates
            if item["name"] == name and item["language"] == language
        ),
        None,
    )
    if template is None:
        return SendReplyResult(TEMPLATE_UNKNOWN)
    cleaned = _clean_params(template, params)
    if cleaned is None:
        return SendReplyResult(TEMPLATE_PARAMS)

    contact = await _live_contact(db, contact_id)
    if contact is None:
        return SendReplyResult(NOT_FOUND)
    claimed = await _take_claim(db, contact, agent_id, current)
    if claimed is not None:
        return claimed

    message_id = str(uuid.uuid4())
    message = Message(
        id=message_id,
        contact_id=contact.id,
        channel="whatsapp",
        direction="outbound",
        sender_id=CHATBOT_PARTY,
        recipient_id=phone,
        body=_render(template["body"], cleaned),
        external_id=None,
        delivery_status=None,
        is_read=True,
        assigned_to=contact.assigned_to,
        sent_by=agent_id,
        author_kind="agent",
        template_name=name,
        template_language=language,
        template_params=cleaned,
    )
    db.add(message)
    # Committed with the claim before the call, as for text sends.
    await db.commit()

    payload = {
        "request_id": message_id,
        "visitor": {"phone": phone, "bsuid": contact.whatsapp_bsuid},
        "template": {"name": name, "language": language, "params": cleaned},
        "agent": {"id": agent_id, "name": agent_name},
    }
    return await _deliver_stored(db, message, contact, "/internal/crm/send-template", payload)
