# 复制到 vendor/WildClawBench/src/agents/noesis.py
# NOESIS_BACKEND_PATCH
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from src.agents.base import AgentExecution, AgentTaskSpec, BaseAgent


class NoesisAgent(BaseAgent):
    @property
    def expects_gateway(self) -> bool:
        return False

    @property
    def transcript_container_path(self) -> str:
        return "/tmp_workspace/noesis_transcript.txt"

    def run_task(self, spec: AgentTaskSpec) -> AgentExecution:
        backend = Path(os.environ["NOESIS_BACKEND_ROOT"]).resolve()
        spec_path = spec.output_dir / "noesis_spec.json"
        result_path = spec.output_dir / "noesis_result.json"
        spec_path.write_text(
            json.dumps(
                {
                    "task_id": spec.task_id,
                    "prompt": spec.prompt,
                    "workspace_path": spec.workspace_path,
                    "timeout_seconds": spec.timeout_seconds,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cmd = [
            os.environ.get("NOESIS_UV", "uv"),
            "run",
            "python",
            "-m",
            "evals.agent.wildclaw.worker",
            "--spec",
            str(spec_path),
            "--result",
            str(result_path),
        ]
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, cwd=backend, capture_output=True, text=True)
        error = None
        if proc.returncode != 0:
            error = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        elif result_path.is_file():
            error = json.loads(result_path.read_text(encoding="utf-8")).get("error")
        else:
            error = "noesis_result.json missing"
        return AgentExecution(elapsed_time=time.perf_counter() - t0, error=error)

    def collect_usage(self, task_id: str, output_dir: Path, elapsed_time: float) -> dict[str, Any]:
        _ = task_id, output_dir, elapsed_time
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "request_count": 0,
        }
