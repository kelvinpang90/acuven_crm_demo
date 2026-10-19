"""The HTML report and the CSV score sheet (docs/ai-eval.md section 4)."""

import csv
import html
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import FEATURE_NAMES
from .features import ERROR, OK, UNAVAILABLE, SampleResult
from .flags import join_flags

SCORE_COLUMNS = (
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
)

# Inline only: the report must open as a single file with nothing loaded from elsewhere.
STYLE = """
body { font-family: sans-serif; margin: 2em; line-height: 1.5; color: #222; }
section.sample { border: 1px solid #ccc; border-radius: 6px; padding: 1em; margin: 1em 0; }
section.cat-error { border-color: #c62828; }
section.cat-unavailable { border-color: #ef6c00; }
section.cat-other { border-color: #6a1b9a; }
.flags { font-weight: bold; color: #b71c1c; background: #ffebee; padding: 0.3em 0.6em; }
.notice { font-weight: bold; color: #e65100; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ddd; padding: 0.4em; text-align: left; vertical-align: top; }
th { width: 12em; background: #f5f5f5; }
.text, pre { white-space: pre-wrap; word-break: break-word; }
pre { background: #f7f7f7; padding: 0.8em; }
"""


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "<ol>" + "".join(f"<li>{_value(item)}</li>" for item in value) + "</ol>"
    if isinstance(value, dict):
        return f'<span class="text">{_escape(json.dumps(value, ensure_ascii=False))}</span>'
    return f'<span class="text">{_escape(value)}</span>'


def _pretty(content: Any) -> str:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return content
    return json.dumps(content, ensure_ascii=False, indent=2)


def _notice(category: str) -> str:
    if category == ERROR:
        return "AI 出错（含输出校验不过）：这个样例按不合格算。"
    if category == UNAVAILABLE:
        return "AI 暂时不可用：没有进打分表，用 --only 重跑。"
    if category != OK:
        return "结果类别不对，样例可能写错了：没有进打分表。"
    return ""


def _sample_block(result: SampleResult) -> str:
    css = result.category if result.category in {OK, ERROR, UNAVAILABLE} else "other"
    parts = [
        f'<section class="sample cat-{css}">',
        f"<h3>{_escape(result.sample_id)}</h3>",
        f"<p>{_escape(result.covers)}</p>",
        "<table>",
        f"<tr><th>结果类别</th><td>{_escape(result.category)}</td></tr>",
        f"<tr><th>request_id</th><td>{_escape(result.request_id or '—')}</td></tr>",
        f"<tr><th>注入样例</th><td>{'Y' if result.injection else 'N'}</td></tr>",
        "</table>",
    ]
    notice = _notice(result.category)
    if notice:
        parts.append(f'<p class="notice">{_escape(notice)}</p>')
    if result.detail:
        parts.append(f"<pre>{_escape(result.detail)}</pre>")
    if result.flags:
        parts.append(f'<p class="flags">自动标记：{_escape(join_flags(result.flags))}</p>')
    else:
        parts.append("<p>自动标记：无</p>")
    if result.outputs:
        parts.append("<h4>模型输出</h4><table>")
        for name, value in result.outputs.items():
            parts.append(f"<tr><th>{_escape(name)}</th><td>{_value(value)}</td></tr>")
        parts.append("</table>")
    for index, content in enumerate(result.user_messages, start=1):
        parts.append(
            f"<details><summary>发给模型的用户消息 {index}</summary>"
            f"<pre>{_escape(_pretty(content))}</pre></details>"
        )
    for index, output in enumerate(result.raw_outputs, start=1):
        if output is not None:
            parts.append(
                f"<details><summary>模型的原始输出 {index}</summary>"
                f"<pre>{_escape(_pretty(output))}</pre></details>"
            )
    parts.append("</section>")
    return "\n".join(parts)


def render_report(
    results: list[SampleResult], *, run_at: datetime, notes: Iterable[str] = ()
) -> str:
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        "<title>AI 功能评测报告</title>",
        f"<style>{STYLE}</style></head><body>",
        "<h1>AI 功能评测报告</h1>",
        f"<p>运行时刻（UTC）：{_escape(run_at.strftime('%Y-%m-%d %H:%M:%S'))}</p>",
    ]
    for note in notes:
        parts.append(f'<p class="notice">{_escape(note)}</p>')
    order = list(FEATURE_NAMES) + sorted(
        {result.feature for result in results} - set(FEATURE_NAMES)
    )
    for feature in order:
        feature_results = [result for result in results if result.feature == feature]
        if not feature_results:
            continue
        parts.append(f"<h2>{_escape(feature)}（{len(feature_results)} 个样例）</h2>")
        parts.extend(_sample_block(result) for result in feature_results)
    parts.append("</body></html>")
    return "\n".join(parts)


def write_report(
    path: Path, results: list[SampleResult], *, run_at: datetime, notes: Iterable[str] = ()
) -> None:
    path.write_text(render_report(results, run_at=run_at, notes=notes), encoding="utf-8")


def score_rows(results: list[SampleResult]) -> list[dict[str, str]]:
    """One row per ``ok`` or ``error`` sample; the reviewer fills in the rest."""
    rows = []
    for result in results:
        if result.category not in {OK, ERROR}:
            continue
        row = {column: "" for column in SCORE_COLUMNS}
        row.update(
            sample_id=result.sample_id,
            feature=result.feature,
            auto_flags=join_flags(result.flags),
            injection_sample="Y" if result.injection else "N",
        )
        rows.append(row)
    return rows


def write_scores(path: Path, results: list[SampleResult]) -> None:
    # UTF-8 with BOM so Excel opens it without garbling Chinese.
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SCORE_COLUMNS)
        writer.writeheader()
        writer.writerows(score_rows(results))
