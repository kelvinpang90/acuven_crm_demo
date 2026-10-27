"""One adapter per AI feature, plus the send_message recorder and the fixed clock.

Each adapter calls the existing business function (docs/ai-eval.md section 2)
with the acting user and the sample's params, and reads the feature's output
fields off the returned ``AiAssistResult``.
"""

import importlib
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import ModuleType
from typing import Any
from unittest.mock import patch

from app.models import User
from app.services import ai_api_client, ai_assist_service

from . import FEATURE_NAMES
from .flags import AI_ERROR, auto_flags
from .seed import SampleError, seed_sample

OK = "ok"
ERROR = "error"
UNAVAILABLE = "unavailable"
# Not a business category: the call raised. Listed like any other wrong sample.
EXCEPTION = "exception"


def _param(params: dict[str, Any], name: str) -> Any:
    if name not in params:
        raise SampleError(f"params 缺少 {name}")
    return params[name]


def _today() -> str:
    # The follow-up function requires today within a day of the server's UTC date.
    return datetime.now(timezone.utc).date().isoformat()


def _brief(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "contact_id": _param(params, "contact"),
        "output_language": _param(params, "output_language"),
    }


def _reply_draft(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "contact_id": _param(params, "contact"),
        "channel": _param(params, "channel"),
        "output_language": _param(params, "output_language"),
        "instruction": params.get("instruction"),
    }


def _translate(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "contact_id": _param(params, "contact"),
        "text": _param(params, "text"),
        "target_language": _param(params, "target_language"),
    }


def _contact_summary(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": user,
        "contact_id": _param(params, "contact"),
        "output_language": _param(params, "output_language"),
    }


def _followup_draft(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": user,
        "contact_id": _param(params, "contact"),
        "deal_id": _param(params, "deal"),
        "note": _param(params, "note"),
        "today": _today(),
        "output_language": _param(params, "output_language"),
    }


def _deal_next_step(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": user,
        "deal_id": _param(params, "deal"),
        "output_language": _param(params, "output_language"),
    }


def _contact_extract(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": user,
        "contact_id": _param(params, "contact"),
        "output_language": _param(params, "output_language"),
    }


def _import_columns(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": user,
        "columns": _param(params, "columns"),
        "output_language": _param(params, "output_language"),
    }


def _message_triage(user: User, params: dict[str, Any]) -> dict[str, Any]:
    # Triggered by the system in the app, so no user is passed (design section 9 item 1).
    del user
    return {
        "contact_id": _param(params, "contact"),
        "message_id": _param(params, "message"),
    }


def _dashboard_insight(user: User, params: dict[str, Any]) -> dict[str, Any]:
    return {"user": user, "output_language": _param(params, "output_language")}


@dataclass(frozen=True)
class Feature:
    name: str
    module_name: str
    function_name: str
    arguments: Callable[[User, dict[str, Any]], dict[str, Any]]
    # Shown in the report, in this order.
    output_fields: tuple[str, ...]
    # Free text written by the model; the only fields the automatic flags scan
    # (section 9 item 7). "tasks.title" is the title of every task draft;
    # "suggestions.value" and "suggestions.evidence" read every extract suggestion.
    text_fields: tuple[str, ...]

    def load(self) -> tuple[ModuleType, Callable[..., Any]]:
        module = importlib.import_module(self.module_name)
        return module, getattr(module, self.function_name)

    def unavailable_reason(self) -> str | None:
        try:
            self.load()
        except (ImportError, AttributeError) as exc:
            return f"{self.module_name}.{self.function_name} 导入失败（{type(exc).__name__}）"
        return None


_ASSIST = "app.services.ai_assist_service"
FEATURES: dict[str, Feature] = {
    feature.name: feature
    for feature in (
        Feature(
            "conversation_brief",
            _ASSIST,
            "generate_conversation_brief",
            _brief,
            (
                "customer_need",
                "conversation_state",
                "customer_mood",
                "open_questions",
                "suggested_next_step",
                "truncated",
            ),
            ("customer_need", "conversation_state", "open_questions", "suggested_next_step"),
        ),
        Feature(
            "reply_draft",
            _ASSIST,
            "generate_reply_draft",
            _reply_draft,
            ("subject", "draft", "note_for_agent", "truncated"),
            ("draft", "subject", "note_for_agent"),
        ),
        Feature(
            "translate",
            _ASSIST,
            "translate_text",
            _translate,
            ("translation", "detected_language"),
            ("translation",),
        ),
        Feature(
            "contact_summary",
            "app.services.contact_summary_service",
            "generate_contact_summary",
            _contact_summary,
            ("summary", "suggested_next_step", "truncated"),
            ("summary", "suggested_next_step"),
        ),
        Feature(
            "followup_draft",
            "app.services.followup_draft_service",
            "generate_followup_draft",
            _followup_draft,
            ("activity_type", "content", "tasks"),
            ("content", "tasks.title"),
        ),
        Feature(
            "deal_next_step",
            "app.services.deal_next_step_service",
            "generate_deal_next_step",
            _deal_next_step,
            (
                "assessment",
                "stall_reason",
                "next_steps",
                "idle_days",
                "days_in_status",
                "stalled",
                "truncated",
            ),
            ("assessment", "stall_reason", "next_steps"),
        ),
        Feature(
            "contact_extract",
            "app.services.contact_extract_service",
            "generate_contact_extract",
            _contact_extract,
            ("suggestions", "truncated"),
            ("suggestions.value", "suggestions.evidence"),
        ),
        # The model only picks a field or null per column: no free text to scan.
        Feature(
            "import_columns",
            "app.services.import_columns_service",
            "suggest_import_columns",
            _import_columns,
            ("mappings",),
            (),
        ),
        # Two enum labels only: no free text to scan.
        Feature(
            "message_triage",
            "app.services.message_triage_service",
            "classify_conversation",
            _message_triage,
            ("intent", "urgency"),
            (),
        ),
        Feature(
            "dashboard_insight",
            "app.services.dashboard_insight_service",
            "generate_dashboard_insight",
            _dashboard_insight,
            ("headline", "observations", "suggestions", "truncated"),
            ("headline", "observations", "suggestions"),
        ),
    )
}
assert tuple(FEATURES) == FEATURE_NAMES


def text_values(feature: Feature, outputs: dict[str, Any]) -> list[str]:
    """Return every free-text value the automatic flags scan for this feature."""
    texts: list[str] = []
    for name in feature.text_fields:
        field_name, _, item_key = name.partition(".")
        value = outputs.get(field_name)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if item_key:
                item = item.get(item_key) if isinstance(item, dict) else None
            if isinstance(item, str):
                texts.append(item)
    return texts


@dataclass
class RecordedCall:
    feature: str | None
    user_messages: list[str]
    outcome: str
    output: dict[str, Any] | None
    request_id: str | None


@contextmanager
def recording_send_message(calls: list[RecordedCall]) -> Iterator[list[RecordedCall]]:
    """Wrap ``ai_api_client.send_message``, forwarding every call unchanged.

    Records the feature, the user messages and the model's raw output; the
    original function is put back on exit.
    """
    original = ai_api_client.send_message

    async def send_message(*args: Any, **kwargs: Any) -> ai_api_client.AiApiResult:
        result = await original(*args, **kwargs)
        calls.append(
            RecordedCall(
                feature=kwargs.get("feature"),
                user_messages=[
                    str(message.get("content", ""))
                    for message in kwargs.get("messages") or []
                    if isinstance(message, dict) and message.get("role") == "user"
                ],
                outcome=result.outcome,
                output=result.output,
                request_id=result.request_id,
            )
        )
        return result

    ai_api_client.send_message = send_message
    try:
        yield calls
    finally:
        ai_api_client.send_message = original


def fixed_datetime(now: datetime) -> type[datetime]:
    """Return a ``datetime`` subclass whose clock is stopped at ``now`` (section 9 item 4)."""
    fixed = now.astimezone(timezone.utc)
    naive = fixed.replace(tzinfo=None)

    class FixedDatetime(datetime):
        @classmethod
        def utcnow(cls) -> datetime:
            return naive

        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return naive if tz is None else fixed.astimezone(tz)

    return FixedDatetime


@contextmanager
def fixed_clock(feature: Feature, now: datetime) -> Iterator[None]:
    """Stop the clock of the feature's module and of ai_assist_service at ``now``."""
    module, _ = feature.load()
    clock = fixed_datetime(now)
    with ExitStack() as stack:
        for target in {id(item): item for item in (module, ai_assist_service)}.values():
            stack.enter_context(patch.object(target, "datetime", clock))
        yield


@dataclass
class SampleResult:
    feature: str
    sample_id: str
    covers: str
    injection: bool
    category: str
    request_id: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)
    user_messages: list[str] = field(default_factory=list)
    raw_outputs: list[dict[str, Any] | None] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    detail: str | None = None


def _arguments(
    feature: Feature, sample_id: str, user: User | None, params: dict[str, Any]
) -> dict[str, Any]:
    if user is None:
        raise SampleError(f"样例 {sample_id}：acting_user 没有建出来")
    try:
        return feature.arguments(user, params)
    except SampleError as exc:
        raise SampleError(f"样例 {sample_id}：{exc}") from exc


async def check_sample(feature: Feature, sample: dict[str, Any]) -> None:
    """Seed the sample and build the call's arguments without calling anything."""
    seeded = await seed_sample(sample, feature.name)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            _arguments(feature, seeded.sample_id, user, seeded.params)
    finally:
        await seeded.dispose()


async def run_sample(
    feature: Feature, sample: dict[str, Any], calls: list[RecordedCall]
) -> SampleResult:
    """Seed one sample, call the feature once, and collect what was sent and returned."""
    seeded = await seed_sample(sample, feature.name)
    result = SampleResult(
        feature=feature.name,
        sample_id=seeded.sample_id,
        covers=str(sample.get("covers") or ""),
        injection=bool(sample.get("injection", False)),
        category=EXCEPTION,
    )
    first_call = len(calls)
    try:
        _, function = feature.load()
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            arguments = _arguments(feature, seeded.sample_id, user, seeded.params)
            clock = fixed_clock(feature, seeded.now) if seeded.fixed_now else nullcontext()
            try:
                with clock:
                    outcome = await function(session, **arguments)
            except Exception as exc:
                result.detail = f"{type(exc).__name__}: {exc}"
            else:
                result.category = outcome.category
                result.request_id = outcome.request_id
                result.outputs = {
                    name: getattr(outcome, name, None) for name in feature.output_fields
                }
    finally:
        await seeded.dispose()

    for call in calls[first_call:]:
        result.user_messages.extend(call.user_messages)
        result.raw_outputs.append(call.output)
    if result.category == OK:
        result.flags = auto_flags(
            text_values(feature, result.outputs),
            result.user_messages,
            list(sample.get("must_not_contain") or []),
        )
    elif result.category == ERROR:
        result.flags = [AI_ERROR]
    return result
