"""Command line: ``python -m evals run`` and ``python -m evals summarize`` (run in backend/).

Rules of ``run`` (docs/ai-eval.md section 3): refuse to start without the four AI
settings or with ``--out`` inside the repository; one sample at a time; ``ok``
and ``error`` go to the score sheet, ``unavailable`` only to the report, and
any other category means a broken sample and a non-zero exit code. Settings
values are never printed or written.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services import ai_api_client

from . import FEATURE_NAMES
from .features import (
    ERROR,
    FEATURES,
    OK,
    UNAVAILABLE,
    Feature,
    RecordedCall,
    SampleResult,
    check_sample,
    recording_send_message,
    run_sample,
)
from .report import write_report, write_scores
from .seed import SampleError
from .summarize import summarize_command

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
EXIT_REFUSED = 2
EXIT_BAD_SAMPLES = 1


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_samples(path: Path) -> list[dict[str, Any]]:
    try:
        samples = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SampleError(f"{path.name} 不是合法的 JSON：{exc}") from exc
    if not isinstance(samples, list) or not all(isinstance(item, dict) for item in samples):
        raise SampleError(f"{path.name} 必须是样例对象的数组")
    return samples


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


async def run(
    out: str | Path,
    *,
    features: list[str] | None = None,
    only: list[str] | None = None,
    samples_dir: Path = SAMPLES_DIR,
    repo_root: Path = REPO_ROOT,
    run_at: datetime | None = None,
) -> int:
    if not ai_api_client.is_ai_configured():
        print(
            "AI 未配置：需要 AI_API_BASE_URL、AI_CLIENT_ID、AI_CLIENT_TOKEN、AI_MODEL 四项"
            "（环境变量或 backend/.env）。没有运行任何样例。"
        )
        return EXIT_REFUSED
    out_dir = Path(out).resolve()
    if _inside(out_dir, repo_root.resolve()):
        print(f"--out 不能在仓库之内（{repo_root.resolve()}），以免报告被误提交。")
        return EXIT_REFUSED
    names = features or list(FEATURE_NAMES)
    unknown = [name for name in names if name not in FEATURES]
    if unknown:
        print(f"不认识的功能：{', '.join(unknown)}；可选：{', '.join(FEATURE_NAMES)}")
        return EXIT_REFUSED

    notes: list[str] = []
    selected: list[tuple[Feature, dict[str, Any]]] = []
    for name in names:
        feature = FEATURES[name]
        reason = feature.unavailable_reason()
        if reason is not None:
            notes.append(f"跳过 {name}：业务函数不可用，{reason}")
            continue
        path = samples_dir / f"{name}.json"
        if not path.is_file():
            notes.append(f"跳过 {name}：没有样例文件 {path.name}")
            continue
        try:
            samples = _load_samples(path)
        except SampleError as exc:
            print(exc)
            return EXIT_BAD_SAMPLES
        selected += [
            (feature, sample) for sample in samples if only is None or sample.get("id") in only
        ]
    for note in notes:
        print(note)

    ids = [sample.get("id") for _, sample in selected]
    duplicated = sorted({str(sample_id) for sample_id in ids if ids.count(sample_id) > 1})
    if duplicated:
        print(f"样例 id 重复：{', '.join(duplicated)}")
        return EXIT_BAD_SAMPLES
    if only is not None:
        not_found = [sample_id for sample_id in only if sample_id not in ids]
        if not_found:
            print(f"--only 里的样例找不到：{', '.join(not_found)}")
            return EXIT_REFUSED
    if not selected:
        print("没有要跑的样例。")
        return EXIT_BAD_SAMPLES
    errors = []
    for feature, sample in selected:
        try:
            await check_sample(feature, sample)
        except SampleError as exc:
            errors.append(str(exc))
    if errors:
        print("样例建不出来，没有调用 AI：")
        for error in errors:
            print(f"- {error}")
        return EXIT_BAD_SAMPLES

    run_at = run_at or datetime.now(timezone.utc)
    target = out_dir / run_at.strftime("%Y%m%dT%H%M%SZ")
    if target.exists():
        print(f"输出目录已存在：{target}")
        return EXIT_REFUSED
    target.mkdir(parents=True)

    results: list[SampleResult] = []
    calls: list[RecordedCall] = []
    with recording_send_message(calls):
        for feature, sample in selected:
            result = await run_sample(feature, sample, calls)
            print(f"{result.feature} {result.sample_id}: {result.category}")
            results.append(result)

    write_report(target / "report.html", results, run_at=run_at, notes=notes)
    write_scores(target / "scores.csv", results)

    counts = {
        category: sum(result.category == category for result in results)
        for category in (OK, ERROR, UNAVAILABLE)
    }
    print("完成：" + "，".join(f"{category} {count}" for category, count in counts.items()))
    unavailable = [result.sample_id for result in results if result.category == UNAVAILABLE]
    if unavailable:
        print(f"暂时不可用、没有进打分表（用 --only 重跑）：{','.join(unavailable)}")
    wrong = [result for result in results if result.category not in {OK, ERROR, UNAVAILABLE}]
    if wrong:
        print("结果类别不对，样例可能写错了：")
        for result in wrong:
            detail = f"（{result.detail}）" if result.detail else ""
            print(f"- {result.sample_id}: {result.category}{detail}")
    print(f"报告：{target / 'report.html'}")
    print(f"打分表：{target / 'scores.csv'}")
    return EXIT_BAD_SAMPLES if wrong else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description="AI 功能质量评测")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="跑样例，写报告与打分表")
    run_parser.add_argument("--out", required=True, help="仓库之外的输出目录")
    run_parser.add_argument("--features", help="逗号分隔的功能名")
    run_parser.add_argument("--only", help="逗号分隔的样例 id")
    summarize_parser = commands.add_parser("summarize", help="读回打分表，算每个功能过没过")
    summarize_parser.add_argument("scores", nargs="+", help="一个或多个 scores.csv，后面的为准")
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(run(args.out, features=_split(args.features), only=_split(args.only)))
    return summarize_command([Path(path) for path in args.scores])


if __name__ == "__main__":
    sys.exit(main())
