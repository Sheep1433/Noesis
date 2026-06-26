"""WildClawBench 单任务 worker（由 vendor 内 noesis backend 子进程调用）。"""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path

from evals.agent._agent import run_deep_research
from config.agent_workspace_paths import ensure_workspace_dir

EVAL_USER = "eval-wildclaw"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--result", type=Path, required=True)
    args = p.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    ws = Path(spec["workspace_path"]).resolve()
    sid = f"wildclaw-{spec.get('task_id', 't')}-{uuid.uuid4().hex[:8]}"
    noesis_ws = ensure_workspace_dir(EVAL_USER, sid)
    if ws.is_dir():
        for entry in ws.iterdir():
            dest = noesis_ws / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, dest)

    run = run_deep_research(
        query=str(spec.get("prompt") or ""),
        session_id=sid,
        user_id=EVAL_USER,
        time_budget_seconds=int(spec.get("timeout_seconds") or 600),
    )
    transcript = noesis_ws / "noesis_transcript.txt"
    transcript.write_text(run.get("final_text") or "", encoding="utf-8")
    if ws.is_dir():
        (ws / "noesis_transcript.txt").write_text(transcript.read_text(encoding="utf-8"), encoding="utf-8")

    payload = {"elapsed_time": run.get("latency_ms", 0) / 1000.0, "error": run.get("error"), "transcript_path": str(transcript)}
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if not payload.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
