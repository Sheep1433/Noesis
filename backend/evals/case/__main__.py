"""CLI: uv run python -m evals.case --tag <name> [--phase testpoints|rag]"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from evals.case.report import (
    default_eval_json_path,
    default_summary_json_path,
    load_promptfoo_eval,
    print_case_summary,
    summarize_promptfoo_eval,
    write_case_summary,
)

CASE_ROOT = Path(__file__).resolve().parent
SHARED_DIR = CASE_ROOT / "shared"
RUN_PYTHON = SHARED_DIR / "run-python.sh"

PHASE_DIRS = {
    "testpoints": CASE_ROOT / "testpoints",
    "rag": CASE_ROOT / "rag",
}

PHASE_ALIASES = {
    "stage-a": "testpoints",
    "stage-b": "rag",
}


def _resolve_phase(phase: str) -> str:
    normalized = PHASE_ALIASES.get(phase, phase)
    if normalized not in PHASE_DIRS:
        valid = sorted(PHASE_DIRS) + sorted(PHASE_ALIASES)
        raise ValueError(f"未知 phase: {phase!r}，可选: {', '.join(valid)}")
    return normalized


def _build_promptfoo_cmd(args: argparse.Namespace, *, output_path: Path) -> list[str]:
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("未找到 npx，请先安装 Node.js")

    phase_dir = PHASE_DIRS[args.phase]
    cmd = [
        npx,
        "promptfoo@latest",
        "eval",
        "-c",
        str(phase_dir / "promptfooconfig.yaml"),
        "--no-share",
        "-o",
        str(output_path),
    ]
    if args.baseline:
        cmd.extend(["--compare", str(args.baseline)])
    if args.item_id:
        cmd.extend(["--filter-metadata", f"item_id={args.item_id}"])
    if args.limit is not None:
        cmd.extend(["-n", str(args.limit)])
    return cmd


def _print_eval_summary(args: argparse.Namespace, output_path: Path) -> None:
    if not output_path.is_file():
        print(f"Warning: 未找到评测结果 JSON，跳过汇总：{output_path}", file=sys.stderr)
        return
    try:
        data = load_promptfoo_eval(output_path)
        summary = summarize_promptfoo_eval(data, tag=args.tag, phase=args.phase)
        summary_path = default_summary_json_path(args.tag, args.phase)
        write_case_summary(summary, summary_path)
        print_case_summary(summary, eval_json=output_path.resolve())
        print(f"Summary JSON: {summary_path.resolve()}")
    except Exception as exc:
        print(f"Warning: 无法生成评测汇总：{exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 测试用例 Agent 离线评测（promptfoo）")
    parser.add_argument("--tag", required=True, help="本次 run 标签，如 baseline / pr-42")
    parser.add_argument(
        "--phase",
        default="testpoints",
        help="testpoints|rag（别名 stage-a|stage-b）",
    )
    parser.add_argument("--baseline", type=Path, default=None, help="promptfoo --compare")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="promptfoo 结果 JSON 路径（默认 evals/case/results/<tag>/<phase>.json）",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅跑前 N 条")
    parser.add_argument("--item-id", type=str, default=None, help="仅跑指定 item_id")
    args = parser.parse_args(argv)

    try:
        args.phase = _resolve_phase(args.phase)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["PROMPTFOO_PYTHON"] = str(RUN_PYTHON)
    env["NOESIS_CASE_EVAL_TAG"] = args.tag

    output_path = (args.output or default_eval_json_path(args.tag, args.phase)).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        cmd = _build_promptfoo_cmd(args, output_path=output_path)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    phase_dir = PHASE_DIRS[args.phase]
    print(f"Running: {' '.join(cmd)}")
    print(f"tag={args.tag} phase={args.phase} config={phase_dir / 'promptfooconfig.yaml'}")
    print(f"output={output_path}")
    proc = subprocess.run(cmd, cwd=phase_dir, env=env)
    _print_eval_summary(args, output_path)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
