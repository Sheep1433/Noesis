"""命令执行内核：bash 后台任务的执行与终态（无 turn / 追加消息概念）。

_arun_shell 经 agent backend 执行命令；_ShellKind 是 kinds.py 行为协议
的 shell 实现（不可追问、立即取消、硬杀超时）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from noesis.agents.background.kinds import StopMode
from noesis.chat.runs import RunStatus
from noesis.runtime.logging import logger

import pathlib

from noesis.agents.background.jobs.registry import (
    _TASKS,
    _TASKS_LOCK,
    _TaskEntry,
    _dequeue_locked,
)
from noesis.agents.background.jobs.settle import (
    TaskTerminal,
    _stop_terminal,
            settle_task_sync,
)
from noesis.agents.backends.sandbox_lifecycle import sandbox_api_path
from noesis.agents.background.jobs.state import (
    BgTaskStatus,
        _SHELL_RESULT_TAIL_CHARS,
)

# 运行中输出流：命令输出重定向到沙箱工作区内的日志文件（cwd=工作区根），
# 轮询经 backend 互斥锁之外的通道读取（runner file-read / 本地文件），
# 每 2s flush 尾部到 bg_shell_job.output_tail 供模型与 web 面查看
_OUTPUT_POLL_SECONDS = 2.0
_OUTPUT_TAIL_SNAPSHOT_CHARS = 4096
_OUTPUT_RING_BUFFER_CHARS = 65_536

async def _mark_shell_row_started(task_id: str) -> None:
    from noesis.agents.background.ports import ShellJobPort

    await ShellJobPort.mark_started(task_id)


def _wrap_command_for_log(command: str, task_id: str) -> str:
    """命令包装：子 shell 执行 + 输出重定向到工作区内日志文件。

    子 shell 保证原命令的退出码就是 wrapped 的退出码（无管道吞码）。
    """
    return (
        f"mkdir -p .task-outputs && ( {command} ) "
        f"> .task-outputs/{task_id}.log 2>&1"
    )


def _read_log_tail(entry: "_TaskEntry", log_rel: str, max_chars: int) -> str:
    """互斥锁之外读取运行日志尾部（阻塞 IO，调用方须在线程中执行）。

    - docker backend：直调 runner file-read API（backend.download_files 持
      会话互斥锁，运行中命令会持锁至结束，必须绕开）
    - local backend：直接读宿主机工作区文件
    """
    task = entry.task
    from noesis.agents.backends.docker_exec import DockerExecSandboxBackend
    from noesis.config.env import SandboxConfig, sandbox_runner_headers
    from noesis.paths import resolve_read_container_path

    if isinstance(entry.shell_backend, DockerExecSandboxBackend):
        import httpx

        container_path = resolve_read_container_path(f"/workspace/{log_rel}")
        url = f"{SandboxConfig.runner_url.rstrip('/')}{sandbox_api_path(task.user_id, task.session_id)}/files/read"
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                headers=sandbox_runner_headers(),
                json={"file": container_path},
            )
        if resp.status_code >= 400:
            return ""
        data = resp.json()
        return str(data.get("content", "") or "")[-max_chars:]
    # local backend（LocalShellBackend 继承 FilesystemBackend，自带 cwd
    # = 工作区根）：从 backend 自身解析日志路径，与写入侧（命令 cwd）
    # 永远同源
    root = getattr(entry.shell_backend, "cwd", None) or getattr(
        entry.shell_backend, "root_dir", None,
    )
    if root is None:
        from noesis.config.user_data_paths import get_workspace_dir

        root = get_workspace_dir(task.user_id, task.session_id)
    host_path = pathlib.Path(root) / log_rel.lstrip("/")
    if not host_path.is_file():
        return ""
    with open(host_path, "rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - max_chars * 4))
        return handle.read().decode("utf-8", errors="replace")[-max_chars:]


async def _poll_shell_output(entry: "_TaskEntry", log_rel: str) -> None:
    """运行中输出尾部轮询：读日志 → 内存快照 → DB flush（主 loop）。

    任务终态即停止；flush 失败仅告警（输出可读性降级，不中断命令）。
    """
    task = entry.task
    while not task.status.is_terminal:
        await asyncio.sleep(_OUTPUT_POLL_SECONDS)
        if task.status.is_terminal:
            break
        try:
            tail = await asyncio.to_thread(
                _read_log_tail, entry, log_rel, _OUTPUT_TAIL_SNAPSHOT_CHARS,
            )
        except Exception:  # noqa: BLE001
            logger.debug("bg shell output poll failed task_id={}", task.task_id)
            continue
        if not tail:
            continue
        task.output_tail = tail
        try:
            from noesis.agents.background.ports import ShellJobPort
            from noesis.runtime.main_loop import run_on_main_loop

            run_on_main_loop(
                ShellJobPort.update_output_tail(task.task_id, tail),
                name=f"bg-shell-output-flush:{task.task_id}",
            )
        except Exception:  # noqa: BLE001
            logger.debug("bg shell output flush failed task_id={}", task.task_id)


async def _read_log_content(entry: "_TaskEntry", log_rel: str) -> str:
    """终态读取日志全文（重定向路径的 task.result 来源），有界防超大输出。"""
    from noesis.agents.background.jobs.state import _SHELL_RESULT_TAIL_CHARS

    text = await asyncio.to_thread(
        _read_log_tail, entry, log_rel, max(_SHELL_RESULT_TAIL_CHARS * 2, 131_072),
    )
    return text


async def _arun_shell(entry: _TaskEntry) -> None:
    """kind="shell"：直接经 backend 执行命令，终态写结果与通知。

    不经 worker 编译、无 turn / 追加消息 / 审批概念；backend 的
    aexecute 即 to_thread(execute)，同步 httpx 客户端线程安全。
    终态发布（SSE + 通知）只在协程自身落终态的分支做——CancelledError
    由触发方（cancel / watchdog / 沙箱销毁）负责发布，这里不重复。
    """
    task = entry.task
    # 事实行 queued→running（started_at）：查询投影与对账口径与内存一致
    from noesis.runtime.main_loop import run_on_main_loop

    run_on_main_loop(
        _mark_shell_row_started(task.task_id),
        name=f"bg-shell-started:{task.task_id}",
    )
    # 运行中输出流：命令重定向到工作区内日志文件，轮询任务周期读尾部
    # （aexecute 是阻塞单响应，运行中输出只存在于该文件）
    log_rel = f".task-outputs/{task.task_id}.log"
    wrapped = _wrap_command_for_log(task.command or "", task.task_id)
    poller = asyncio.create_task(_poll_shell_output(entry, log_rel))
    try:
        timeout = entry.shell_command_timeout
        response = await entry.shell_backend.aexecute(
            wrapped,
            **({"timeout": timeout} if timeout is not None else {}),
        )
        # 命令输出在日志文件里（重定向），从文件取全文供 result_tail；
        # 文件读不到（假 backend/沙箱消失）时回退 response.output
        file_output = await _read_log_content(entry, log_rel)
        task.result = _format_shell_result(response, file_output or None)
        settle_task_sync(
            entry,
            TaskTerminal(
                task_status=BgTaskStatus.COMPLETED,
                run_status=RunStatus.COMPLETED,
                finish_reason="stop",
            ),
        )
        logger.info(
            "bg shell task completed task_id={} exit_code={} duration={:.1f}s",
            task.task_id,
            getattr(response, "exit_code", None),
            task.completed_at - task.started_at,
        )
    except asyncio.CancelledError:
        # cancel / 超时 / 沙箱销毁：终态由触发方设置并发布；此处仅确认
        # 状态已落（未落则兜底标记，不重复发布）。容器内进程由
        # sandbox-runner 终止或随容器回收（尽力而为）
        poller.cancel()
        if not task.status.is_terminal:
            settle_task_sync(entry, _stop_terminal(entry))
    except Exception as exc:
        poller.cancel()
        settle_task_sync(
            entry,
            TaskTerminal(
                task_status=BgTaskStatus.FAILED,
                run_status=RunStatus.ERROR,
                finish_reason="error",
                error=str(exc),
            ),
        )
        logger.opt(exception=True).error(
            "bg shell task failed task_id={}", task.task_id,
        )
    else:
        # 成功路径：停掉输出轮询（终态 flush 由 mark_terminal 后的
        # 最后一次轮询循环退出兜底）
        poller.cancel()

def _format_shell_result(response: Any, output: str | None = None) -> str:
    """ExecuteResponse → check_async_task 结果文本（exit code + 有界输出尾部）。

    output 缺省取 response.output（非重定向路径）；重定向路径由调用方
    传入日志文件全文。
    """
    output = output if output is not None else str(getattr(response, "output", "") or "")
    tail = output[-_SHELL_RESULT_TAIL_CHARS:]
    parts = [f"exit code: {getattr(response, 'exit_code', None)}"]
    if len(tail) < len(output):
        parts.append(f"（输出超长，仅保留尾部 {_SHELL_RESULT_TAIL_CHARS} 字符）")
    if getattr(response, "truncated", False):
        parts.append("（sandbox 截断了输出）")
    if tail:
        parts.append(tail)
    return "\n".join(parts)

def fail_session_shell_tasks(session_id: str, reason: str) -> None:
    """会话沙箱销毁时运行中 shell 任务转 failed（容器回收连坐）。

    同样适用于 subagent 任务（其工具也在容器里执行），一并终结避免
    挂死在已销毁的执行环境上。
    """
    with _TASKS_LOCK:
        entries = [
            e for e in _TASKS.values()
            if e.task.session_id == session_id and not e.task.status.is_terminal
        ]
        # 排队任务先出队再连坐：否则循环内每个终态通知都会触发 drain，
        # 把排队任务调度进刚销毁的沙箱
        for entry in entries:
            if entry.task.status == BgTaskStatus.QUEUED:
                _dequeue_locked(entry.task)
    for entry in entries:
        # 跨线程竞态：协程可能在列举之后刚好落终态——先复查再连坐，
        # 避免覆盖 COMPLETED 并造成双重通知
        if entry.task.status.is_terminal:
            continue
        if entry.future is not None and not entry.future.done():
            entry.future.cancel()
        settle_task_sync(
            entry,
            TaskTerminal(
                task_status=BgTaskStatus.FAILED,
                run_status=RunStatus.ERROR,
                finish_reason="sandbox_destroyed",
                error=reason,
            ),
        )
    if entries:
        logger.warning(
            "bg tasks failed on session sandbox destroy session_id={} count={}",
            session_id, len(entries),
        )

class _ShellKind:
    """后台命令：不可追问、无轮次、立即取消、硬杀超时。"""

    kind = "shell"
    supports_message_append = False
    has_turns = False

    @staticmethod
    def reject_append_text() -> str:
        return "该任务为后台命令任务，不支持追加消息（可用 check_async_task 收取输出、重新执行请新建命令）"

    @staticmethod
    def run(entry: "_TaskEntry") -> Any:
        return _arun_shell(entry)

    @staticmethod
    def request_stop(entry: "_TaskEntry") -> "StopMode":
        return StopMode.IMMEDIATE_CANCEL

    @staticmethod
    def on_timeout_locked(entry: "_TaskEntry") -> bool:
        return True  # 硬杀：命令在 backend 不可中断
