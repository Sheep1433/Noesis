"""Harbor 自定义 Agent 入口（Harbor Python 进程，子进程拉起 backend Worker）。"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from evals.agent.harbor.harbor_env_proxy import HarborEnvironmentProxy
from evals.agent.harbor.noesis_artifacts import load_run_summary

_AGENT_VERSION = "0.1.0"


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


class NoesisHarborAgent(BaseAgent):
    """Host 侧薄适配：环境代理 + `uv run` 子进程运行 Noesis Agent。"""

    SUPPORTS_ATIF: bool = True

    @staticmethod
    def name() -> str:
        return "noesis-harbor"

    def version(self) -> str | None:
        return _AGENT_VERSION

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        session_id = environment.session_id or str(uuid.uuid4())
        logs_dir = self.logs_dir.resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)
        instruction_path = logs_dir / "instruction.txt"
        instruction_path.write_text(instruction, encoding="utf-8")
        worker_log_path = logs_dir / "noesis-worker.log"

        proxy = HarborEnvironmentProxy(environment)
        await proxy.start()
        host, _, port_str = proxy.url.partition(":")
        port = int(port_str)

        backend_root = _backend_root()
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "evals.agent.harbor.noesis_worker",
            "--proxy-host",
            host,
            "--proxy-port",
            str(port),
            "--instruction-file",
            str(instruction_path),
            "--logs-dir",
            str(logs_dir),
            "--session-id",
            session_id,
            "--model-name",
            self.model_name or "",
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = str(backend_root) + (
            f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(backend_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        log_lines: list[str] = []
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                log_lines.append(text)
            return_code = await proc.wait()
        finally:
            await proxy.stop()
            worker_log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

        summary = load_run_summary(logs_dir)
        context.n_input_tokens = summary.get("tokens", {}).get("input") or None
        context.n_output_tokens = summary.get("tokens", {}).get("output") or None
        context.metadata = {
            "tool_stats": summary.get("tool_stats") or {},
            "final_text_preview": str(summary.get("final_text") or "")[:500],
            "worker_return_code": return_code,
        }

        if return_code != 0:
            error = summary.get("error") or f"worker exited with code {return_code}"
            context.metadata["error"] = error
            raise RuntimeError(error)
