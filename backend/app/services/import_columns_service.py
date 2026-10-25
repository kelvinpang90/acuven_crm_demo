"""AI column recognition for spreadsheet contact import (docs/contact-import-columns.md 4.2)."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services import ai_api_client
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    INVALID_INPUT,
    LANGUAGES,
    NOT_CONFIGURED,
    OK,
    AiAssistResult,
    write_ai_request,
)
from app.services.ai_features import (
    IMPORT_COLUMNS_FEATURE,
    IMPORT_COLUMNS_SCHEMA,
    IMPORT_COLUMNS_SYSTEM,
    IMPORT_FIELDS,
    MAX_TOKENS,
    validate_import_columns,
)

MAX_COLUMNS = 40
MAX_SAMPLES = 3
MAX_SAMPLE_LENGTH = 50
MAX_HEADER_LENGTH = 100
# The nine characters trimmed from both ends, identical in frontend and backend (design 9.16).
BLANKS = " \t\n\r\x0b\x0c 　﻿"


def strip_blanks(value: str) -> str:
    return value.strip(BLANKS)


def _normalize_columns(columns: Any) -> list[dict[str, Any]] | None:
    if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_COLUMNS:
        return None
    normalized = []
    seen: set[str] = set()
    for column in columns:
        if not isinstance(column, dict):
            return None
        header = column.get("header")
        samples = column.get("samples")
        if not isinstance(header, str) or not isinstance(samples, list):
            return None
        header = strip_blanks(header)
        if not 1 <= len(header) <= MAX_HEADER_LENGTH or header in seen:
            return None
        if len(samples) > MAX_SAMPLES or any(
            not isinstance(sample, str) or len(sample) > MAX_SAMPLE_LENGTH for sample in samples
        ):
            return None
        seen.add(header)
        normalized.append({"header": header, "samples": list(samples)})
    return normalized


async def suggest_import_columns(
    db: AsyncSession, *, user: User, columns: Any, output_language: str
) -> AiAssistResult:
    """Suggest a CRM field per column and audit every request which reaches the AI client."""
    normalized = _normalize_columns(columns)
    if (
        normalized is None
        or not isinstance(output_language, str)
        or output_language not in LANGUAGES
    ):
        return AiAssistResult(INVALID_INPUT)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)

    result = await ai_api_client.send_message(
        feature=IMPORT_COLUMNS_FEATURE,
        system=IMPORT_COLUMNS_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": compact_json(
                    {
                        "output_language": output_language,
                        "columns": normalized,
                        "fields": list(IMPORT_FIELDS),
                    }
                ),
            }
        ],
        max_tokens=MAX_TOKENS,
        schema=IMPORT_COLUMNS_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_import_columns(result.output, [column["header"] for column in normalized])
        if output is not None:
            await write_ai_request(
                db,
                user_id=user.id,
                contact_id=None,
                feature=IMPORT_COLUMNS_FEATURE,
                request_id=result.request_id,
                outcome=OK,
            )
            return AiAssistResult(OK, request_id=result.request_id, **output)
        outcome = ERROR
    else:
        outcome = result.outcome
    await write_ai_request(
        db,
        user_id=user.id,
        contact_id=None,
        feature=IMPORT_COLUMNS_FEATURE,
        request_id=result.request_id,
        outcome=outcome,
    )
    return AiAssistResult(outcome, request_id=result.request_id)
