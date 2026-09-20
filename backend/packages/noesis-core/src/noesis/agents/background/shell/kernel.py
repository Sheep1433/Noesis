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
from noesis.agents.background.jobs.state import (
    BgTaskStatus,
        _SHELL_RESULT_TAIL_CHARS,
)

async def _mark_shell_row_started(task_id: str) -> None:
    from noesis.agents.background.ports import ShellJobPort

    await ShellJobPort.mark_started(task_id)


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
    try:
        timeout = entry.shell_command_timeout
        response = await entry.shell_backend.aexecute(
            task.command or "",
            **({"timeout": timeout} if timeout is not None else {}),
        )
        task.result = _format_shell_result(response)
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
        if not task.status.is_terminal:
            settle_task_sync(entry, _stop_terminal(entry))
    except Exception as exc:
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

def _format_shell_result(response: Any) -> str:
    """ExecuteResponse → check_async_task 结果文本（exit code + 有界输出尾部）。"""
    output = str(getattr(response, "output", "") or "")
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
