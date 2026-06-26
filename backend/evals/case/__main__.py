"""CLI: uv run python -m evals.case --tag <name> [--phase testpoints|rag]"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

CASE_ROOT = Path(__file__).resolve().parent
SHARED_DIR = CASE_ROOT / "shared"
RUN_PYTHON = SHARED_DIR / "run-python.sh"

PHASE_DIRS = {
    "testpoints": CASE_ROOT / "testpoints",
    "rag": CASE_ROOT / "rag",
}


def _build_promptfoo_cmd(args: argparse.Namespace) -> list[str]:
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
    ]
    if args.baseline:
        cmd.extend(["--compare", str(args.baseline)])
    if args.output:
        cmd.extend(["-o", str(args.output)])
    if args.item_id:
        cmd.extend(["--filter-metadata", f"item_id={args.item_id}"])
    if args.limit is not None:
        cmd.extend(["-n", str(args.limit)])
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 测试用例 Agent 离线评测（promptfoo）")
    parser.add_argument("--tag", required=True, help="本次 run 标签，如 baseline / pr-42")
    parser.add_argument(
        "--phase",
        choices=sorted(PHASE_DIRS),
        default="testpoints",
        help="testpoints 测试点 | rag 检索",
    )
    parser.add_argument("--baseline", type=Path, default=None, help="promptfoo --compare")
    parser.add_argument("--output", type=Path, default=None, help="结果 JSON 路径")
    parser.add_argument("--limit", type=int, default=None, help="仅跑前 N 条")
    parser.add_argument("--item-id", type=str, default=None, help="仅跑指定 item_id")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env["PROMPTFOO_PYTHON"] = str(RUN_PYTHON)
    env["NOESIS_CASE_EVAL_TAG"] = args.tag
    env["NOESIS_CASE_EVAL_PHASE"] = args.phase
    if args.item_id:
        env["NOESIS_CASE_EVAL_ITEM_ID"] = args.item_id
    if args.limit is not None:
        env["NOESIS_CASE_EVAL_LIMIT"] = str(args.limit)

    try:
        cmd = _build_promptfoo_cmd(args)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    phase_dir = PHASE_DIRS[args.phase]
    print(f"Running: {' '.join(cmd)}")
    print(f"tag={args.tag} phase={args.phase} config={phase_dir / 'promptfooconfig.yaml'}")
    proc = subprocess.run(cmd, cwd=phase_dir, env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
