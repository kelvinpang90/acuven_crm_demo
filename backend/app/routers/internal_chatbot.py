"""Conversation events pushed by ai_chatbot_demo (docs/chatbot-events.md section 4).

Reachable only on the shared Docker network; nginx forwards nothing under
/internal/. The signature is checked against the raw body before the body is
parsed. A handled event answers `{"result": ...}` at the top level, without the
usual `ok()` wrapper: the chatbot reads only the top-level `result`.
"""

import hashlib
import hmac
import logging
import re
import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.chatbot_event import ChatbotEvent
from app.services import chatbot_event_service
from app.utils.response import fail

router = APIRouter()
logger = logging.getLogger("crm.chatbot_events")

MAX_CLOCK_SKEW_SECONDS = 300
SIGNATURE_PREFIX = "sha256="
_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_HEX_DIGEST = re.compile(r"[0-9a-fA-F]{64}")


def _now() -> int:
    return int(time.time())


def _configured_secrets() -> list[str]:
    candidates = (settings.chatbot_events_secret, settings.chatbot_events_secret_previous)
    return [s for s in candidates if s]


def signature_ok(
    timestamp: Optional[str], signature: Optional[str], raw_body: bytes, secrets: list[str]
) -> bool:
    """HMAC-SHA256 over `<timestamp>.<raw body>` with any configured secret's
    UTF-8 bytes, compared in constant time; the timestamp (Unix seconds) must be
    within 300 seconds of now."""
    if not timestamp or not signature:
        return False
    if not _TIMESTAMP.fullmatch(timestamp):
        return False
    if abs(_now() - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
        return False
    if not signature.startswith(SIGNATURE_PREFIX):
        return False
    given = signature[len(SIGNATURE_PREFIX) :]
    if not _HEX_DIGEST.fullmatch(given):
        return False
    given = given.lower()
    signed = timestamp.encode("ascii") + b"." + raw_body
    matched = False
    for secret in secrets:
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        # No early exit: every configured key is compared.
        matched |= hmac.compare_digest(expected, given)
    return matched


@router.post("/events")
async def chatbot_event(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_acuven_timestamp: Annotated[Optional[str], Header()] = None,
    x_acuven_signature: Annotated[Optional[str], Header()] = None,
):
    secrets = _configured_secrets()
    if not secrets:
        return fail(
            "Chatbot events secret is not configured", code="NOT_CONFIGURED", status_code=503
        )

    raw_body = await request.body()
    if not signature_ok(x_acuven_timestamp, x_acuven_signature, raw_body, secrets):
        return fail("Invalid signature or timestamp", code="UNAUTHORIZED", status_code=401)

    try:
        event = ChatbotEvent.model_validate_json(raw_body)
    except ValidationError as exc:
        errors = exc.errors()
        fields = {".".join(str(p) for p in e["loc"]) or "body": e["msg"] for e in errors}
        logger.warning("chatbot event rejected: %s", sorted(fields))
        return fail("Invalid event body", code="INVALID_EVENT", fields=fields, status_code=422)

    result = await chatbot_event_service.process_event(db, **event.service_kwargs())
    await db.commit()
    logger.info("chatbot event %s type=%s result=%s", event.event_id, event.type, result)
    return JSONResponse({"result": result})
