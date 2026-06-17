"""CLI: uv run python -m evals --tag <name> [--scope full] [--baseline <path>]

委托 promptfoo 执行离线评测。等价于在 backend/evals/promptfoo 下运行 `npx promptfoo eval`。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

EVALS_ROOT = Path(__file__).resolve().parent
PROMPTFOO_DIR = EVALS_ROOT / "promptfoo"
BACKEND_ROOT = EVALS_ROOT.parent
RUN_PYTHON = PROMPTFOO_DIR / "run-python.sh"


def _build_promptfoo_cmd(args: argparse.Namespace) -> list[str]:
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("未找到 npx，请先安装 Node.js")

    cmd = [
        npx,
        "promptfoo@latest",
        "eval",
        "-c",
        str(PROMPTFOO_DIR / "promptfooconfig.yaml"),
        "--no-share",
    ]
    if args.baseline:
        cmd.extend(["--compare", str(args.baseline)])
    if args.output:
        cmd.extend(["-o", str(args.output)])
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 测试用例 Agent 离线评测（promptfoo）")
    parser.add_argument("--tag", required=True, help="本次 run 标签，如 baseline / pr-42")
    parser.add_argument(
        "--scope",
        choices=("testpoints", "cases", "full"),
        default="full",
        help="执行范围：testpoints / cases / full",
    )
    parser.add_argument("--dataset", type=Path, default=None, help="dataset.jsonl 路径")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="与历史 promptfoo 结果 JSON 对比（promptfoo --compare）",
    )
    parser.add_argument("--output", type=Path, default=None, help="promptfoo 结果输出 JSON 路径")
    parser.add_argument("--limit", type=int, default=None, help="仅跑前 N 条")
    parser.add_argument("--item-id", type=str, default=None, help="仅跑指定 id")
    parser.add_argument(
        "--mock-judge",
        action="store_true",
        help="coverage 使用名称匹配 mock Judge（不调 DashScope）",
    )
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env["PROMPTFOO_PYTHON"] = str(RUN_PYTHON)
    env["NOESIS_EVAL_TAG"] = args.tag
    env["NOESIS_EVAL_SCOPE"] = args.scope
    if args.item_id:
        env["NOESIS_EVAL_ITEM_ID"] = args.item_id
    if args.limit is not None:
        env["NOESIS_EVAL_LIMIT"] = str(args.limit)
    if args.mock_judge:
        env["NOESIS_EVAL_MOCK_JUDGE"] = "1"
    if args.dataset:
        env["NOESIS_EVAL_DATASET"] = str(args.dataset.resolve())

    try:
        cmd = _build_promptfoo_cmd(args)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(f"Running: {' '.join(cmd)}")
    print(f"scope={args.scope} tag={args.tag}")
    proc = subprocess.run(cmd, cwd=PROMPTFOO_DIR, env=env)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
