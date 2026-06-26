"""WildClawBench 官方评测：uv run python -m evals.agent.wildclaw --tag <name> [-- 上游参数]"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PATCH_MARKER = "# NOESIS_BACKEND_PATCH"
SUITE_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = SUITE_ROOT / "results"
REPO_ROOT = SUITE_ROOT.parents[3]
BACKEND_ROOT = SUITE_ROOT.parents[2]


def _wildclaw_root() -> Path | None:
    env = os.environ.get("WILDCLAWBENCH_ROOT") or os.environ.get("NOESIS_WILDCLAWBENCH_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "script" / "run.sh").is_file():
            return p
    for p in (REPO_ROOT / "vendor" / "WildClawBench",):
        if (p / "script" / "run.sh").is_file():
            return p.resolve()
    return None


def _apply_patch(root: Path) -> list[str]:
    actions: list[str] = []
    patch = SUITE_ROOT / "patch"
    (root / "src" / "agents").mkdir(parents=True, exist_ok=True)
    dest = root / "src" / "agents" / "noesis.py"
    dest.write_text((SUITE_ROOT / "noesis_agent.py").read_text(encoding="utf-8"), encoding="utf-8")
    actions.append(f"installed {dest.name}")

    run_sh = root / "script" / "run.sh"
    if PATCH_MARKER not in run_sh.read_text(encoding="utf-8"):
        text = run_sh.read_text(encoding="utf-8")
        text = text.replace(
            'echo "Expected one of: openclaw, claudecode, codex, hermesagent"',
            'echo "Expected one of: openclaw, claudecode, codex, hermesagent, noesis"',
        )
        if "noesis)" not in text:
            snippet = (patch / "run_sh_snippet.sh").read_text(encoding="utf-8").strip()
            text = text.replace("set -euo pipefail", f"set -euo pipefail\n{PATCH_MARKER}", 1)
            text = text.replace("    *)", f"    {snippet}\n\n    *)", 1)
        run_sh.write_text(text, encoding="utf-8")
        actions.append("patched script/run.sh")

    run_batch = root / "eval" / "run_batch.py"
    if 'agent_backend == "noesis"' not in run_batch.read_text(encoding="utf-8"):
        anchor = '    if args.agent_backend == "claudecode":'
        block = (patch / "run_batch_snippet.py").read_text(encoding="utf-8").rstrip()
        text = run_batch.read_text(encoding="utf-8").replace(anchor, block + "\n\n" + anchor, 1)
        run_batch.write_text(text, encoding="utf-8")
        actions.append("patched eval/run_batch.py")
    return actions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="WildClawBench（官方 script/run.sh + Docker grader）")
    p.add_argument("--tag", required=True)
    p.add_argument("extra", nargs=argparse.REMAINDER, help="传给 run.sh noesis，前导 -- 可选")
    args = p.parse_args(argv)

    root = _wildclaw_root()
    if root is None:
        print(
            "未找到 WildClawBench。请：\n"
            "  git clone https://github.com/InternLM/WildClawBench vendor/WildClawBench\n"
            "或设置 WILDCLAWBENCH_ROOT",
            file=sys.stderr,
        )
        return 2

    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra:
        extra = ["--category", "all", "--parallel", "1"]

    patch_actions = _apply_patch(root)
    cmd = ["bash", str(root / "script" / "run.sh"), "noesis", *extra]
    env = os.environ.copy()
    env["NOESIS_BACKEND_ROOT"] = str(BACKEND_ROOT)
    env["NOESIS_EVAL_TAG"] = args.tag

    proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)
    out = RESULTS_ROOT / args.tag.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)
    log = out / "run.log"
    log.write_text(
        f"$ {' '.join(cmd)}\n\n=== stdout ===\n{proc.stdout}\n\n=== stderr ===\n{proc.stderr}\n",
        encoding="utf-8",
    )
    summary = {
        "benchmark": "wildclaw",
        "tag": args.tag,
        "exit_code": proc.returncode,
        "command": cmd,
        "wildclaw_root": str(root),
        "patch_actions": patch_actions,
        "upstream": "InternLM/WildClawBench",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        print(f"失败，详见 {log}", file=sys.stderr)
        return proc.returncode

    print(f"WildClawBench 完成，日志: {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
