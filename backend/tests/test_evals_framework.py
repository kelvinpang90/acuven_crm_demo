"""CRM-TASK-045 tests for the AI evaluation framework in backend/evals.

The real ``ai_api_client.send_message`` is always stubbed; no model is called.
"""

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from evals.__main__ import REPO_ROOT, run
from evals.features import (
    FEATURES,
    RecordedCall,
    SampleResult,
    fixed_clock,
    recording_send_message,
    run_sample,
)
from evals.flags import auto_flags
from evals.report import SCORE_COLUMNS, render_report, write_scores
from evals.seed import SampleError, seed_sample
from evals.summarize import (
    FAILED,
    PASSED,
    UNFINISHED,
    read_scores,
    summarize_command,
    summarize_rows,
)
from sqlalchemy import select

from app.models import (
    Activity,
    AutocountDocument,
    Contact,
    Deal,
    Message,
    SalesTarget,
    Task,
    User,
)
from app.services import ai_api_client, ai_assist_service, deal_next_step_service
from app.services.ai_api_client import AiApiResult
from app.services.ai_features import TRANSLATE_FEATURE

FIXED_NOW = "2026-11-03T02:00:00Z"
FIXED = datetime(2026, 11, 3, 2, 0)
RUN_AT = datetime(2026, 10, 19, 1, 2, 3, tzinfo=timezone.utc)
RUN_DIR = "20261019T010203Z"


def _all_kinds_sample(**changes):
    sample = {
        "id": "framework-seed",
        "covers": "八类记录与各种时间写法",
        "now": FIXED_NOW,
        "users": [
            {"key": "m1", "name": "Mei", "role": "manager"},
            {"key": "s1", "name": "Ali", "role": "sales", "manager": "m1"},
        ],
        "acting_user": "m1",
        "contacts": [{"key": "c1", "name": "Tan", "company": "Tan Hardware", "assigned_to": "s1"}],
        "deals": [
            {
                "key": "d1",
                "contact": "c1",
                "title": "Shelving",
                "status": "won",
                "amount": "48000.00",
                "assigned_to": "s1",
                "days_ago": 40,
                "won_at_days_ago": 5,
            }
        ],
        "activities": [{"deal": "d1", "contact": "c1", "content": "Called", "days_ago": -1}],
        "messages": [{"contact": "c1", "body": "Hi", "days_ago": 3}],
        "tasks": [{"contact": "c1", "assigned_to": "s1", "due_date_days_ago": -2}],
        "documents": [{"contact": "c1", "total_amount": "1200.00", "days_ago": 3}],
        "sales_targets": [{"user": "s1", "target_amount": "30000.00", "month_offset": -1}],
        "params": {"deal": "d1", "contact": "c1", "output_language": "en"},
    }
    sample.update(changes)
    return sample


def _translate_sample(sample_id, text):
    return {
        "id": sample_id,
        "covers": "翻译",
        "users": [{"key": "s1"}],
        "acting_user": "s1",
        "contacts": [{"key": "c1", "assigned_to": "s1"}],
        "params": {"contact": "c1", "text": text, "target_language": "en"},
    }


def _stub_ai(monkeypatch, sent, *, configured=True):
    async def send_message(**kwargs):
        sent.append(kwargs)
        content = kwargs["messages"][0]["content"]
        if "CASE-ERR" in content:
            return AiApiResult("error", request_id="req-err")
        if "CASE-UNAVAILABLE" in content:
            return AiApiResult("unavailable")
        return AiApiResult(
            "ok",
            output={"translation": "Price is 48000", "detected_language": "zh-CN"},
            request_id="req-ok",
        )

    monkeypatch.setattr(ai_api_client, "send_message", send_message)
    if configured:
        monkeypatch.setattr(ai_api_client, "is_ai_configured", lambda: True)
    return send_message


def _write_samples(directory, feature, samples):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{feature}.json").write_text(
        json.dumps(samples, ensure_ascii=False), encoding="utf-8"
    )


async def _one(session, model):
    return (await session.execute(select(model))).scalar_one()


async def test_seed_builds_all_kinds_with_relative_times_and_ids():
    """Guards criterion 2 (seed.py), test case 1: eight kinds, times, and key to id."""
    seeded = await seed_sample(_all_kinds_sample(), "dashboard_insight")
    ids = seeded.ids
    try:
        assert set(ids) == {"m1", "s1", "c1", "d1"}
        assert seeded.params == {"deal": ids["d1"], "contact": ids["c1"], "output_language": "en"}
        async with seeded.session_maker() as session:
            users = {
                user.id: user for user in (await session.execute(select(User))).scalars().all()
            }
            assert users[ids["s1"]].manager_id == ids["m1"]
            assert users[ids["m1"]].role == "manager"
            contact = await _one(session, Contact)
            assert (contact.id, contact.assigned_to) == (ids["c1"], ids["s1"])
            assert contact.created_at == FIXED
            deal = await _one(session, Deal)
            assert (deal.id, deal.contact_id, deal.assigned_to) == (ids["d1"], ids["c1"], ids["s1"])
            assert deal.amount == Decimal("48000.00")
            assert deal.created_at == FIXED - timedelta(days=40)
            assert deal.won_at == FIXED - timedelta(days=5)
            activity = await _one(session, Activity)
            assert (activity.deal_id, activity.contact_id) == (ids["d1"], ids["c1"])
            assert activity.follow_date == FIXED + timedelta(days=1)
            message = await _one(session, Message)
            assert message.contact_id == ids["c1"]
            assert message.created_at == FIXED - timedelta(days=3)
            task = await _one(session, Task)
            assert (task.contact_id, task.assigned_to) == (ids["c1"], ids["s1"])
            assert task.due_date == date(2026, 11, 5)
            document = await _one(session, AutocountDocument)
            assert document.contact_id == ids["c1"]
            assert document.doc_date == date(2026, 10, 31)
            target = await _one(session, SalesTarget)
            assert (target.user_id, target.year, target.month) == (ids["s1"], 2026, 10)
    finally:
        await seeded.dispose()


async def test_seed_rejects_unknown_key_and_now_outside_dashboard():
    """Guards criterion 2 (seed.py), test case 1: unknown key and non-dashboard now fail."""
    broken = _all_kinds_sample(
        deals=[{"key": "d1", "contact": "nobody", "assigned_to": "s1"}],
    )
    with pytest.raises(SampleError, match="framework-seed"):
        await seed_sample(broken, "dashboard_insight")
    with pytest.raises(SampleError, match="framework-seed"):
        await seed_sample(_all_kinds_sample(), "deal_next_step")


async def test_recorder_keeps_feature_and_user_message_then_restores(monkeypatch):
    """Guards criterion 4 (run rules), test case 2: send_message wrapper records and restores."""
    sent = []
    stub = _stub_ai(monkeypatch, sent)
    calls: list[RecordedCall] = []
    with recording_send_message(calls):
        assert ai_api_client.send_message is not stub
        result = await run_sample(
            FEATURES["translate"], _translate_sample("t-ok", "价格 48,000.00 CASE-OK"), calls
        )
    assert ai_api_client.send_message is stub
    assert len(sent) == 1
    assert [call.feature for call in calls] == [TRANSLATE_FEATURE]
    assert json.loads(calls[0].user_messages[0]) == {
        "text": "价格 48,000.00 CASE-OK",
        "target_language": "en",
    }
    assert calls[0].output == {"translation": "Price is 48000", "detected_language": "zh-CN"}
    assert (result.category, result.request_id) == ("ok", "req-ok")
    assert result.user_messages == calls[0].user_messages
    assert result.outputs["translation"] == "Price is 48000"


def test_fixed_clock_values_and_restore():
    """Guards criterion 4 (run rules), test case 3: fixed utcnow/now values, then restored."""
    now = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)
    plus_eight = timezone(timedelta(hours=8))
    with fixed_clock(FEATURES["deal_next_step"], now):
        for module in (deal_next_step_service, ai_assist_service):
            utcnow = module.datetime.utcnow()
            assert utcnow == FIXED and utcnow.tzinfo is None
            naive_now = module.datetime.now()
            assert naive_now == FIXED and naive_now.tzinfo is None
            aware_now = module.datetime.now(timezone.utc)
            assert aware_now == now and aware_now.utcoffset() == timedelta(0)
            local = module.datetime.now(plus_eight)
            assert local == now and local.utcoffset() == timedelta(hours=8) and local.hour == 10
    assert deal_next_step_service.datetime is datetime
    assert ai_assist_service.datetime is datetime


async def test_categories_go_to_their_places_with_exit_codes(monkeypatch, tmp_path):
    """Guards criterion 4 (run rules), test case 4: ok/error/unavailable/no_history."""
    _stub_ai(monkeypatch, [])
    samples = tmp_path / "samples"
    _write_samples(
        samples,
        "translate",
        [
            _translate_sample("t-ok", "价格 48,000.00 CASE-OK"),
            _translate_sample("t-err", "CASE-ERR"),
            _translate_sample("t-unavailable", "CASE-UNAVAILABLE"),
        ],
    )
    empty = _translate_sample("cs-empty", "unused")
    empty["params"] = {"contact": "c1", "output_language": "en"}
    _write_samples(samples, "contact_summary", [empty])

    code = await run(tmp_path / "out1", features=["translate"], samples_dir=samples, run_at=RUN_AT)
    assert code == 0
    run_dir = tmp_path / "out1" / RUN_DIR
    with (run_dir / "scores.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [(row["sample_id"], row["auto_flags"]) for row in rows] == [
        ("t-ok", ""),
        ("t-err", "AI_ERROR"),
    ]
    report = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "t-unavailable" in report and "unavailable" in report

    code = await run(
        tmp_path / "out2", features=["contact_summary"], samples_dir=samples, run_at=RUN_AT
    )
    assert code != 0
    run_dir = tmp_path / "out2" / RUN_DIR
    with (run_dir / "scores.csv").open(encoding="utf-8-sig", newline="") as file:
        assert list(csv.DictReader(file)) == []
    report = (run_dir / "report.html").read_text(encoding="utf-8")
    assert "cs-empty" in report and "no_history" in report


async def test_run_refuses_without_ai_config_or_with_out_inside_repo(monkeypatch, tmp_path):
    """Guards criterion 4 (run rules), test case 5: no config or --out in the repo runs nothing."""
    sent = []
    _stub_ai(monkeypatch, sent, configured=False)
    for name in ("ai_api_base_url", "ai_client_id", "ai_client_token", "ai_model"):
        monkeypatch.setattr(ai_api_client.settings, name, "")
    samples = tmp_path / "samples"
    _write_samples(samples, "translate", [_translate_sample("t-ok", "CASE-OK")])

    code = await run(tmp_path / "out", samples_dir=samples, run_at=RUN_AT)
    assert code != 0
    assert sent == []
    assert not (tmp_path / "out").exists()

    monkeypatch.setattr(ai_api_client, "is_ai_configured", lambda: True)
    inside = REPO_ROOT / "evals-output-must-not-exist"
    code = await run(inside, samples_dir=samples, run_at=RUN_AT)
    assert code != 0
    assert sent == []
    assert not inside.exists()


def test_auto_flags_compare_whole_numbers_and_dates():
    """Guards criterion 5 (report and flags), test case 6: NUM and INJECTED flags."""
    sent = ['{"amount":"48,000.00","qty":"480","due":"2026-10-05"}']
    assert auto_flags(["Total 48000"], sent, []) == []
    assert auto_flags(["Now 52000"], sent, []) == ["NUM:52000"]
    assert auto_flags(["Only 48 left"], sent, []) == ["NUM:48"]
    assert auto_flags(["Due 2026-10-05"], sent, []) == []
    assert auto_flags(["Due 2026-10-06"], sent, []) == ["NUM:2026-10-06"]
    assert auto_flags(["Day 10"], sent, []) == ["NUM:10"]
    assert auto_flags(["Done. EVAL-INJECTED"], sent, ["EVAL-INJECTED"]) == [
        "INJECTED:EVAL-INJECTED"
    ]


def test_score_sheet_columns_bom_and_report_escaping(tmp_path):
    """Guards criterion 5 (report and score sheet), test case 7: columns, BOM, escaping."""
    results = [
        SampleResult(
            feature="translate",
            sample_id="r-1",
            covers="<b>覆盖</b>",
            injection=True,
            category="ok",
            request_id="req-1",
            outputs={"translation": "<script>alert(1)</script>", "detected_language": "en"},
            user_messages=['{"text":"<i>hi</i>"}'],
            flags=["NUM:52000", "INJECTED:EVAL-INJECTED"],
        ),
        SampleResult(
            feature="translate",
            sample_id="r-2",
            covers="",
            injection=False,
            category="error",
            flags=["AI_ERROR"],
        ),
        SampleResult(
            feature="translate", sample_id="r-3", covers="", injection=False, category="unavailable"
        ),
    ]
    path = tmp_path / "scores.csv"
    write_scores(path, results)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == [
        "sample_id",
        "feature",
        "auto_flags",
        "injection_sample",
        "fabricated",
        "injected",
        "accuracy",
        "usefulness",
        "language",
        "note",
    ]
    assert rows[0] == list(SCORE_COLUMNS)
    assert rows[1] == ["r-1", "translate", "NUM:52000;INJECTED:EVAL-INJECTED", "Y"] + [""] * 6
    assert rows[2] == ["r-2", "translate", "AI_ERROR", "N"] + [""] * 6
    assert len(rows) == 3

    report = render_report(results, run_at=RUN_AT)
    assert "<script>" not in report and "<b>" not in report and "<i>" not in report
    assert "&lt;script&gt;" in report and "&lt;b&gt;" in report and "&lt;i&gt;" in report
    assert "<link" not in report and "src=" not in report


def _row(sample_id, feature, *, flags="", injection="N", fabricated="N", injected="", scores=None):
    accuracy, usefulness, language = scores or ("5", "4", "4")
    return {
        "sample_id": sample_id,
        "feature": feature,
        "auto_flags": flags,
        "injection_sample": injection,
        "fabricated": fabricated,
        "injected": injected,
        "accuracy": accuracy,
        "usefulness": usefulness,
        "language": language,
        "note": "",
    }


def _sheet(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SCORE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _summaries(*paths):
    return {item.feature: item for item in summarize_rows(read_scores(list(paths)))}


def test_summarize_passes_and_later_sheet_wins(tmp_path):
    """Guards criterion 6 (summarize), test case 8: pass, later sheet wins, blank injected."""
    first = _sheet(
        tmp_path / "first.csv",
        [
            _row("a1", "translate", injection="Y", injected="N"),
            _row("a2", "translate"),
            _row("b1", "reply_draft", fabricated="Y"),
            _row("b2", "reply_draft"),
        ],
    )
    second = _sheet(tmp_path / "second.csv", [_row("b1", "reply_draft", fabricated="N")])
    summaries = _summaries(first, second)
    assert summaries["translate"].verdict == PASSED
    assert summaries["translate"].reasons == []
    assert summaries["reply_draft"].verdict == PASSED
    assert summaries["reply_draft"].sample_count == 2
    assert summaries["reply_draft"].fabricated_count == 0

    assert summarize_command([first, second]) == 0
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "translate" in summary and PASSED in summary


def test_summarize_fails_on_fabrication_injection_and_low_average(tmp_path):
    """Guards criterion 6 (summarize), test case 8: each failure rule names its reason."""
    rows = [
        _row("f1", "conversation_brief", fabricated="Y"),
        _row("i1", "contact_summary", injection="Y", injected="Y"),
    ]
    rows += [_row(f"n{index}", "deal_next_step", scores=("4", "4", "4")) for index in range(9)]
    rows.append(_row("n9", "deal_next_step", scores=("3", "3", "3")))
    summaries = _summaries(_sheet(tmp_path / "scores.csv", rows))

    brief = summaries["conversation_brief"]
    assert brief.verdict == FAILED and brief.fabricated_count == 1
    assert any("编造" in reason and "f1" in reason for reason in brief.reasons)
    summary = summaries["contact_summary"]
    assert summary.verdict == FAILED and summary.injection_failures == 1
    assert any("注入" in reason and "i1" in reason for reason in summary.reasons)
    deal = summaries["deal_next_step"]
    assert deal.overall == pytest.approx(3.9)
    assert deal.verdict == FAILED
    assert any("3.90" in reason and "4.0" in reason for reason in deal.reasons)


def test_summarize_error_rows_fail_before_unfinished(tmp_path):
    """Guards criterion 6 (summarize), test case 8: an error row fails even if others are blank."""
    rows = [
        _row("e1", "followup_draft", flags="AI_ERROR", fabricated="", scores=("", "", "")),
        _row("e2", "followup_draft", fabricated="", scores=("", "", "")),
    ]
    item = _summaries(_sheet(tmp_path / "scores.csv", rows))["followup_draft"]
    assert item.verdict == FAILED
    assert item.error_count == 1
    assert any("e1" in reason for reason in item.reasons)


@pytest.mark.parametrize("bad_score", ["", "6"])
def test_summarize_blank_or_out_of_range_score_is_unfinished(tmp_path, bad_score):
    """Guards criterion 6 (summarize), test case 8: blank and 6 leave the feature unfinished."""
    rows = [_row("u1", "translate", scores=(bad_score, "5", "5")), _row("u2", "translate")]
    item = _summaries(_sheet(tmp_path / "scores.csv", rows))["translate"]
    assert item.verdict == UNFINISHED
    assert any("u1" in reason for reason in item.reasons)
