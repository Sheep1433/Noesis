"""以子进程驱动 noesis CLI：评测与被测对象之间只隔 argv + env + stream-json。

契约（对齐 harbor/terminal-bench 的「CLI 即被测对象」模式）：
- 每样本一个全新 CLI 进程——跨样本无共享事件循环 / 连接池 / 全局状态；
- 被测凭据经 evals/.env 的 NOESIS_API_KEY + NOESIS_BASE_URL 注入（env 直连），
  不经数据库用户模型解析；
- 输出 stream-json 流，wall-clock 超时由本 driver 持有：杀进程但保留已收到
  的部分结果（超时题照样可判卷）。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from noesis_cli.streamjson import StreamCollector, flag_empty_completion
from noesis.config.user_data_paths import get_workspace_dir

BACKEND_DIR = Path(__file__).resolve().parents[2]
EVAL_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
_STDERR_TAIL_CHARS = 2000
# 终稿短于此长度时按契约路径收报告文件（正常报告篇幅远高于此）
_HARVEST_MIN_CHARS = 1000
_REPORT_FILENAME = "final-report.md"
_MAX_STREAM_LINE_BYTES = 16 * 1024 * 1024


def eval_model_env() -> dict[str, str]:
    """被测模型 env 直连配置：进程环境优先，其次 evals/.env。"""
    raw = dotenv_values(EVAL_ENV_FILE) if EVAL_ENV_FILE.is_file() else {}
    overrides: dict[str, str] = {}
    for key in ("NOESIS_API_KEY", "NOESIS_BASE_URL", "NOESIS_MODEL_TYPE", "NOESIS_MODEL"):
        value = os.environ.get(key) or str(raw.get(key) or "")
        if value.strip():
            overrides[key] = value.strip()
    return overrides


async def run_cli_agent(
    *,
    query: str,
    session_id: str,
    user_id: str,
    model: str | None = None,
    qa_type: str = "super",
    time_budget_seconds: int = 600,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """跑一题：spawn `noesis chat -p ... --output-format stream-json`，返回评测记录。"""
    argv = [
        "uv", "run", "noesis", "chat", "-p", query,
        "--output-format", "stream-json",
        "--session-id", session_id,
        "--qa-type", qa_type,
    ]
    if model:
        argv += ["--model", model]
    env = dict(os.environ)
    env.update(eval_model_env())
    # 控制台脚本的 sys.path 不含 cwd：补 PYTHONPATH 让 CLI 进程能 import
    # backend 的 server 包（wire_langfuse 的观测绑定实现所在）
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(BACKEND_DIR), env.get("PYTHONPATH", "")) if p
    )
    env["NOESIS_USER_ID"] = user_id
    # 评测环境不产生 runner 沙箱容器（用户已定）：工具在本地执行，
    # 与 docker 沙箱生产语义存在已知偏差
    env["SANDBOX_BACKEND"] = "local_shell"
    # 长思考/长生成端点超时：默认 30s/120s 会让首个请求 APITimeoutError
    # 直接空收场（deepresearch 线同款值）；已在环境里显式设置时不覆盖
    env.setdefault("REQUEST_TIMEOUT", "600")
    env.setdefault("STREAM_IDLE_TIMEOUT", "300")
    env.update(extra_env or {})

    collector = StreamCollector()
    stderr_tail = ""
    t0 = time.perf_counter()
    timeout_error: str | None = None

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        # 默认行上限 64KB 不够：LongMemEval 工具输出单行 JSON 可达数 MB
        limit=_MAX_STREAM_LINE_BYTES,
    )

    async def consume_stdout() -> None:
        nonlocal stderr_tail
        assert proc.stdout is not None and proc.stderr is not None
        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            async for chunk in proc.stderr:
                stderr_chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    collector.consume(json.loads(line))
                except ValueError:
                    stderr_chunks.append(f"[stdout-noise] {line}\n".encode())
        finally:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            stderr_tail = b"".join(stderr_chunks).decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:]

    try:
        await asyncio.wait_for(consume_stdout(), timeout=time_budget_seconds)
    except asyncio.TimeoutError:
        timeout_error = f"timeout after {time_budget_seconds}s"
        _kill_process_group(proc)
    except Exception as exc:  # noqa: BLE001
        # 流读取等意外故障：记为该样本的 error，保留已收到的部分结果，
        # 不让单题炸掉整批（后续样本照跑）
        timeout_error = f"driver failure: {exc!r}"
        _kill_process_group(proc)
    exit_code = await proc.wait()

    record = collector.to_record()
    record.update(
        session_id=session_id,
        user_id=user_id,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        cli_exit_code=exit_code,
        stderr_tail=stderr_tail.strip(),
    )
    if timeout_error:
        record["completed"] = False
        record["error"] = timeout_error
    elif exit_code not in (0, 1) and not record["error"]:
        # 0=成功 1=运行失败（已带 error）；其他值=CLI 自身崩溃（参数错等）
        record["completed"] = False
        record["error"] = f"cli exited {exit_code}"
    record = flag_empty_completion(record)
    # 交付契约的备选形态兜底：终稿过短时按约定路径收 /workspace/final-report.md
    # （契约见 EVAL_MODE_SUFFIX；不猜文件——计划/笔记/多报告都会让启发式误判）
    if not record.get("error") and len(str(record.get("final_text") or "")) < _HARVEST_MIN_CHARS:
        report = Path(get_workspace_dir(user_id, session_id)) / _REPORT_FILENAME
        if report.is_file() and report.stat().st_size > _HARVEST_MIN_CHARS:
            try:
                record["final_text"] = report.read_text(encoding="utf-8")
                record["article_source"] = f"workspace_file:{_REPORT_FILENAME}"
            except OSError:
                pass
    return record


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """杀整个进程组（uv run 下还有子 python），孤儿进程会继续烧 token。"""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
