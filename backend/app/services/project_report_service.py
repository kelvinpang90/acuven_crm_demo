"""AI report drafts for roofing projects (docs/project-ai-report.md sections 3, 4.1-4.3 and 5)."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, ProjectStepHistory
from app.models.user import User
from app.services import ai_api_client
from app.services.ai_api_client import compact_json
from app.services.ai_assist_service import (
    ERROR,
    INVALID_INPUT,
    LANGUAGES,
    NOT_CONFIGURED,
    NOT_FOUND,
    OK,
    STEP_NOT_REACHED,
    AiAssistResult,
    _as_utc_string,
    write_ai_request,
)
from app.services.ai_features import (
    MAX_TOKENS,
    PROJECT_REPORT_FEATURE,
    PROJECT_REPORT_SCHEMA,
    PROJECT_REPORT_SYSTEM,
    validate_project_report,
)

# Same keys, in the same order, as frontend steps.ts; step_no N is STEP_KEYS[N - 1].
STEP_KEYS = (
    "enquiry",
    "inspection_scheduled",
    "inspection_done",
    "quotation_sent",
    "confirmed",
    "deposit_received",
    "work_scheduled",
    "in_progress",
    "quality_check",
    "completion_photos",
    "handover_payment",
    "warranty_active",
)
# The lowest current_step at which each report type may be drafted (design section 1).
REPORT_MIN_STEP = {"inspection": 3, "completion": 10}
MAX_NOTES_LENGTH = 2000
MIN_UTC_OFFSET = -720
MAX_UTC_OFFSET = 840


def _step_key(step_no: int) -> str | None:
    return STEP_KEYS[step_no - 1] if 1 <= step_no <= len(STEP_KEYS) else None


async def build_report_steps(
    db: AsyncSession, project: Project, utc_offset_minutes: int
) -> list[dict[str, Any]]:
    """One entry per step reached, up to current_step (design section 3 and section 9)."""
    rows = (
        (
            await db.execute(
                select(ProjectStepHistory)
                .where(
                    ProjectStepHistory.project_id == project.id,
                    ProjectStepHistory.step_no >= 1,
                    ProjectStepHistory.step_no <= project.current_step,
                )
                .order_by(
                    ProjectStepHistory.step_no,
                    ProjectStepHistory.entered_at,
                    ProjectStepHistory.id,
                )
            )
        )
        .scalars()
        .all()
    )
    offset = timedelta(minutes=utc_offset_minutes)
    steps: list[dict[str, Any]] = []
    for row in rows:
        if not steps or steps[-1]["step_no"] != row.step_no:
            # The earliest row of a step gives its date: the day the step was first reached.
            steps.append(
                {
                    "step_no": row.step_no,
                    "step": _step_key(row.step_no),
                    "date": (row.entered_at + offset).date().isoformat(),
                    "note": None,
                }
            )
        note = row.note.strip() if isinstance(row.note, str) else ""
        if note:
            # Rows are ascending, so the last non-blank note is the newest one.
            steps[-1]["note"] = note
    return steps


def _normalize_notes(notes: Any) -> tuple[bool, str | None]:
    if notes is None:
        return True, None
    if not isinstance(notes, str):
        return False, None
    notes = notes.strip()
    if len(notes) > MAX_NOTES_LENGTH:
        return False, None
    return True, notes or None


async def generate_project_report(
    db: AsyncSession,
    *,
    user: User,
    project_id: str,
    report_type: Any,
    notes: Any,
    utc_offset_minutes: Any,
    output_language: Any,
) -> AiAssistResult:
    """Draft a validated project report and audit every request which reaches the AI client."""
    project = await db.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        return AiAssistResult(NOT_FOUND)
    notes_valid, site_notes = _normalize_notes(notes)
    if (
        not isinstance(report_type, str)
        or report_type not in REPORT_MIN_STEP
        or not notes_valid
        or not isinstance(utc_offset_minutes, int)
        or isinstance(utc_offset_minutes, bool)
        or not MIN_UTC_OFFSET <= utc_offset_minutes <= MAX_UTC_OFFSET
        or not isinstance(output_language, str)
        or output_language not in LANGUAGES
    ):
        return AiAssistResult(INVALID_INPUT)
    if project.current_step < REPORT_MIN_STEP[report_type]:
        return AiAssistResult(STEP_NOT_REACHED)
    if not ai_api_client.is_ai_configured():
        return AiAssistResult(NOT_CONFIGURED)

    payload = {
        "as_of": _as_utc_string(datetime.utcnow()),
        "output_language": output_language,
        "report_type": report_type,
        "project": {
            "customer_name": project.customer_name,
            "address": project.address,
            "service_type": project.service_type,
            "project_manager": project.project_manager,
            "current_step": project.current_step,
            "current_step_key": _step_key(project.current_step),
        },
        "steps": await build_report_steps(db, project, utc_offset_minutes),
        "site_notes": site_notes,
    }
    result = await ai_api_client.send_message(
        feature=PROJECT_REPORT_FEATURE,
        system=PROJECT_REPORT_SYSTEM,
        messages=[{"role": "user", "content": compact_json(payload)}],
        max_tokens=MAX_TOKENS,
        schema=PROJECT_REPORT_SCHEMA,
    )
    if result.outcome == ai_api_client.OK:
        output = validate_project_report(result.output)
        if output is not None:
            await write_ai_request(
                db,
                user_id=user.id,
                contact_id=None,
                feature=PROJECT_REPORT_FEATURE,
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
        feature=PROJECT_REPORT_FEATURE,
        request_id=result.request_id,
        outcome=outcome,
    )
    return AiAssistResult(outcome, request_id=result.request_id)
