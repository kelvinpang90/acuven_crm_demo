"""Client for the Billing Hub Messages API."""

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "not_configured"
OK = "ok"
ERROR = "error"
UNAVAILABLE = "unavailable"
REQUEST_LIMIT_BYTES = 64 * 1024


@dataclass(frozen=True)
class AiApiResult:
    outcome: str
    output: dict[str, Any] | None = None
    request_id: str | None = None


def compact_json(value: Any) -> str:
    """Serialize exactly as the request and snapshot size limits require."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def is_ai_configured() -> bool:
    return all(
        (
            settings.ai_api_base_url,
            settings.ai_client_id,
            settings.ai_client_token,
            settings.ai_model,
        )
    )


def _log(
    feature: str, outcome: str, *, status: int | None = None, request_id: str | None = None
) -> None:
    logger.info(
        "AI request feature=%s status=%s request_id=%s outcome=%s",
        feature,
        status,
        request_id,
        outcome,
    )


async def send_message(
    *,
    feature: str,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    schema: dict[str, Any],
) -> AiApiResult:
    """Send one request without retrying and map its result to CRM outcomes."""
    if not is_ai_configured():
        _log(feature, NOT_CONFIGURED)
        return AiApiResult(NOT_CONFIGURED)

    payload = {
        "feature": feature,
        "model": settings.ai_model,
        "system": system,
        "messages": messages,
        "max_tokens": max_tokens,
        "output_config": {"format": "json_schema", "schema": schema, "effort": "low"},
    }
    content = compact_json(payload)
    if len(content.encode("utf-8")) > REQUEST_LIMIT_BYTES:
        _log(feature, ERROR)
        return AiApiResult(ERROR)

    headers = {
        "X-Acuven-Client-Id": settings.ai_client_id,
        "Authorization": f"Bearer {settings.ai_client_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(50.0)) as client:
            response = await client.post(
                f"{settings.ai_api_base_url.rstrip('/')}/v1/messages",
                content=content.encode("utf-8"),
                headers=headers,
            )
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RequestError):
        _log(feature, UNAVAILABLE)
        return AiApiResult(UNAVAILABLE)

    if response.status_code in {429, 502, 503, 504}:
        _log(feature, UNAVAILABLE, status=response.status_code)
        return AiApiResult(UNAVAILABLE)
    if response.status_code != 200:
        _log(feature, ERROR, status=response.status_code)
        return AiApiResult(ERROR)

    try:
        response_body = response.json()
    except (ValueError, json.JSONDecodeError):
        _log(feature, ERROR, status=200)
        return AiApiResult(ERROR)
    request_id = response_body.get("request_id") if isinstance(response_body, dict) else None
    if not isinstance(response_body, dict) or response_body.get("stop_reason") != "end_turn":
        _log(feature, ERROR, status=200, request_id=request_id)
        return AiApiResult(ERROR, request_id=request_id)
    try:
        output = json.loads(response_body.get("text", ""))
    except (TypeError, json.JSONDecodeError):
        _log(feature, ERROR, status=200, request_id=request_id)
        return AiApiResult(ERROR, request_id=request_id)
    if not isinstance(output, dict):
        _log(feature, ERROR, status=200, request_id=request_id)
        return AiApiResult(ERROR, request_id=request_id)
    _log(feature, OK, status=200, request_id=request_id)
    return AiApiResult(OK, output=output, request_id=request_id)
