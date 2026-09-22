"""以子进程驱动 noesis CLI：评测与被测对象之间只隔 argv + env + stdout 流。

契约（对齐 harbor/terminal-bench 的「CLI 即被测对象」模式）：
- 每样本一个全新 CLI 进程——跨样本无共享事件循环 / 连接池 / 全局状态；
- CLI 走生产 headless 入口：消息/run/token 落库可查，stdout 为生产同源
  SSE 流（raw 底账 = 可重放），终值以 CLI 的 ``__tw_result__``（DB 权威
  组装）为准；工具事件从 SSE 载荷提取；
- wall-clock 超时由本 driver 持有：杀进程但保留已收到的部分结果
  （超时题照样可判卷；DB 中 run 行遗留 running 由下次 server 启动对账收口）。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from noesis.config.user_data_paths import get_workspace_dir

BACKEND_DIR = Path(__file__).resolve().parents[2]
_STDERR_TAIL_CHARS = 2000
# 终稿短于此长度时按契约路径收报告文件（正常报告篇幅远高于此）
_HARVEST_MIN_CHARS = 1000
_REPORT_FILENAME = "final-report.md"
_MAX_STREAM_LINE_BYTES = 16 * 1024 * 1024


class SseToolCollector:
    """从 SSE 帧 data 载荷提取工具调用与 session 级统计（与前端同源事件）。"""

    def __init__(self) -> None:
        self.tool_stats: dict[str, int] = {}
        self.tool_outputs: list[dict[str, Any]] = []
        self._pending: dict[str, tuple[str | None, Any]] = {}
        # stats-update 为累计快照，取最后一帧；uncached_input_tokens 含
        # 子 Agent 的 LLM 调用——是全 session 真实计费口径（主 run 的
        # usage 只覆盖主 Agent）
        self.session_usage: dict[str, Any] = {}

    def consume_data(self, payload: dict[str, Any]) -> None:
        ptype = str(payload.get("type") or "")
        if ptype == "tool-input-available":
            tid = str(payload.get("tool_call_id") or "")
            self._pending[tid] = (payload.get("name"), payload.get("input"))
        elif ptype == "tool-output-available":
            tid = str(payload.get("tool_call_id") or "")
            name, tool_input = self._pending.pop(tid, (payload.get("name"), None))
            resolved = str(name or "unknown")
            self.tool_stats[resolved] = self.tool_stats.get(resolved, 0) + 1
            self.tool_outputs.append({
                "name": resolved,
                "input": tool_input,
                "output": str(payload.get("output") or ""),
            })
        elif ptype == "stats-update":
            self.session_usage = dict(payload)


async def run_cli_agent(
    *,
    query: str,
    session_id: str,
    user_id: str,
    model: str | None = None,
    qa_type: str = "super",
    kb_collections: list[str] | None = None,
    web_search: bool = True,
    time_budget_seconds: int = 600,
    extra_env: dict[str, str] | None = None,
    raw_dump: Path | None = None,
) -> dict[str, Any]:
    """跑一题：spawn `noesis chat -p ... --output-format stream-json`，返回评测记录。

    model 须为内置目录 id 或该账号的自定义模型复合 id（如
    provider/model），经生产模型解析（会话 extra → 用户偏好 → 平台默认）。

    qa_type / kb_collections / web_search 透传 CLI（super 默认；common 场景
    用 kb_collections 限定集合、web_search=False 关闭联网）。

    raw_dump：CLI 的原始 stdout 逐行落盘（评测结果的可重放底账）——
    driver 层采集出 bug 或结果误删时，从原始流的 ``__tw_result__`` 行
    零成本重建（``--recollect``），不必重跑评测。
    """
    argv = [
        "uv", "run", "noesis", "chat", "-p", query,
        "--output-format", "stream-json",
        "--session-id", session_id,
        "--qa-type", qa_type,
    ]
    if kb_collections:
        argv += ["--kb-collections", ",".join(kb_collections)]
    if not web_search:
        argv.append("--no-web-search")
    if model:
        argv += ["--model", model]
    env = dict(os.environ)
    # 控制台脚本的 sys.path 不含 cwd：补 PYTHONPATH 让 CLI 进程能 import
    # backend 的 server 包（wire_langfuse 的观测绑定实现所在）
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(BACKEND_DIR), env.get("PYTHONPATH", "")) if p
    )
    env["NOESIS_USER_ID"] = user_id
    # 沙箱 local_shell 与模型超时放宽不再走环境变量（配置面已收敛）：
    # 评测子进程由 noesis.config.env._EVALS_PROCESS 识别并自动应用
    # （2026-09-08 评测 CLI 化决策：不产生 runner 沙箱容器，存在已知偏差）
    env.update(extra_env or {})

    sse_tools = SseToolCollector()
    db_result: dict[str, Any] | None = None
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
        nonlocal stderr_tail, db_result
        assert proc.stdout is not None and proc.stderr is not None
        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            async for chunk in proc.stderr:
                stderr_chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())

        def consume_line(line: str) -> None:
            nonlocal db_result
            if line.startswith(("event:", "data:", ":")):
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[len("data:"):].strip())
                    except ValueError:
                        return
                    if isinstance(payload, dict):
                        sse_tools.consume_data(payload)
                return
            try:
                obj = json.loads(line)
            except ValueError:
                stderr_chunks.append(f"[stdout-noise] {line}\n".encode())
                return
            if isinstance(obj, dict) and obj.get("type") == "__tw_result__":
                db_result = obj

        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if raw_dump is not None:
                    raw_dump.parent.mkdir(parents=True, exist_ok=True)
                    with raw_dump.open("a", encoding="utf-8") as rf:
                        rf.write(raw_line.decode("utf-8", errors="replace"))
                consume_line(line)
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

    # 终值以 CLI 的 __tw_result__（DB 权威组装）为准；超时/崩溃拿不到
    # 时本 record 只报错误——权威数据仍在 DB，可后续补收
    record: dict[str, Any] = dict(db_result or {})
    record.setdefault("completed", False)
    record.setdefault("final_text", "")
    record.setdefault("error", None)
    record["tool_stats"] = sse_tools.tool_stats
    record["tool_outputs"] = sse_tools.tool_outputs
    if sse_tools.session_usage:
        record["session_usage"] = sse_tools.session_usage
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
