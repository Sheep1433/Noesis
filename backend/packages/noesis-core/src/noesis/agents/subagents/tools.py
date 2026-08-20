"""后台子 Agent 的模型侧工具（start / check / cancel / send / list）。

单工具同异步：``start_task`` 的 ``run_in_background``（默认 true）由模型
按依赖选择——后台立即返回 task_id；前台等待终态并把结果作为工具返回值，
超过 ``foreground_max_wait_seconds`` 自动转后台（同步转异步）。
session/user 在装配时闭包捕获。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from langchain_core.tools import StructuredTool

from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BgTaskStatus,
)
from noesis.config.env import SubagentConfig
from noesis.runtime.logging import logger

_CHECK_PENDING_HINT = {
    BgTaskStatus.RUNNING: "仍在运行",
    BgTaskStatus.AWAITING_APPROVAL: "等待用户审批",
}


def _format_task(task: dict[str, Any]) -> str:
    status = task["status"]
    if status == BgTaskStatus.COMPLETED.value:
        return f"[{task['task_id']}] completed：\n{task.get('result') or '(无结果文本)'}"
    if status in (BgTaskStatus.FAILED.value, BgTaskStatus.TIMED_OUT.value):
        return f"[{task['task_id']}] {status}：{task.get('error') or ''}"
    if status == BgTaskStatus.CANCELLED.value:
        return f"[{task['task_id']}] cancelled"
    hint = _CHECK_PENDING_HINT.get(BgTaskStatus(status), status)
    return f"[{task['task_id']}] {hint}（description: {task['description']}）"


def build_background_task_tools(
    *,
    worker_factory: Callable[[], Any],
    executor: BackgroundSubagentExecutor,
    session_id: str,
    user_id: str,
) -> list[StructuredTool]:
    """构造绑定到当前会话的后台任务工具集。

    ``worker_factory`` 在隔离 loop 内惰性调用（async 可等待），产出带
    checkpointer 的 task-worker runnable；LLM 客户端与连接池随之绑定
    隔离 loop。
    """

    async def astart_task(
        description: str,
        run_in_background: bool = True,
        one_shot: bool = False,
    ) -> str:
        try:
            task_id = executor.start(
                worker_factory=worker_factory, description=description,
                session_id=session_id, user_id=user_id, one_shot=one_shot,
            )
        except ValueError as exc:
            return f"启动失败：{exc}"
        if run_in_background:
            return (
                f"后台任务已启动：{task_id}\n"
                "无需等待——可继续其他工作，之后用 check_task 收结果。"
            )
        # 前台等待：执行仍走后台路径，跨 loop 等待终态；
        # shield 保证超时取消不波及底层任务（自动转后台）
        future = BackgroundSubagentExecutor.get_future(task_id)
        if future is None:
            return f"后台任务已启动：{task_id}（前台等待不可用，稍后 check_task 收结果）"
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=SubagentConfig.foreground_max_wait_seconds,
            )
        except asyncio.TimeoutError:
            return (
                f"任务运行超过 {int(SubagentConfig.foreground_max_wait_seconds)}s，已自动转为后台：{task_id}\n"
                "可继续其他工作，之后用 check_task 收结果。"
            )
        task = BackgroundSubagentExecutor.get(task_id) or {"task_id": task_id, "status": "unknown"}
        status = task.get("status")
        if status == BgTaskStatus.COMPLETED.value:
            return f"任务完成：\n{task.get('result') or '(无结果文本)'}"
        if status == BgTaskStatus.AWAITING_APPROVAL.value:
            return (
                f"任务等待用户审批（{task_id}）。审批通过后任务继续后台运行，"
                "稍后用 check_task 收结果。"
            )
        if status in (BgTaskStatus.FAILED.value, BgTaskStatus.TIMED_OUT.value):
            return f"任务{status}：{task.get('error') or ''}"
        return _format_task(task)

    def start_task(description: str, run_in_background: bool = True, one_shot: bool = False) -> str:
        # 同步入口：langgraph 工具实际走 coroutine；保留同步回退
        if run_in_background:
            try:
                task_id = executor.start(
                    worker_factory=worker_factory, description=description,
                    session_id=session_id, user_id=user_id, one_shot=one_shot,
                )
            except ValueError as exc:
                return f"启动失败：{exc}"
            return (
                f"后台任务已启动：{task_id}\n"
                "无需等待——可继续其他工作，之后用 check_task 收结果。"
            )
        return "前台等待需异步执行环境；请使用 run_in_background=true。"

    def check_task(task_id: str) -> str:
        task = executor.get(task_id)
        if task is None:
            # 内存 miss 时 get 已查持久层；到这里说明任务 ID 确实未知
            return f"{task_id} 不存在"
        if task["session_id"] != session_id:
            return f"{task_id} 不属于当前会话"
        return _format_task(task)

    async def acheck_task(task_id: str) -> str:
        return check_task(task_id)

    def cancel_task(task_id: str) -> str:
        try:
            task = executor.cancel(task_id)
        except ValueError as exc:
            return f"取消失败：{exc}"
        return f"已取消：{task['task_id']}（{task['status']}）"

    async def acancel_task(task_id: str) -> str:
        return cancel_task(task_id)

    def send_message(task_id: str, message: str) -> str:
        try:
            executor.send_message(task_id, message)
        except ValueError as exc:
            return f"发送失败：{exc}"
        return (
            f"消息已提交：{task_id}（作为子任务的新一轮执行，"
            "运行中任务在当前轮结束后生效；已完成任务立即续跑）"
        )

    async def asend_message(task_id: str, message: str) -> str:
        return send_message(task_id, message)

    def list_tasks() -> str:
        tasks = executor.list_for_session(session_id)
        if not tasks:
            return "当前会话没有后台任务"
        return "\n".join(_format_task(t) for t in tasks)

    async def alist_tasks() -> str:
        return list_tasks()

    start = StructuredTool.from_function(
        func=start_task,
        coroutine=astart_task,
        name="start_task",
        description=(
            "启动一个子 Agent 执行较重的独立子任务（多轮检索/调研/长命令）。"
            "description 写清子目标、约束与期望输出格式。"
            "run_in_background（默认 true）：立即返回 task_id，可继续其他工作，之后用 check_task 收结果；"
            "false：前台等待结果直接返回——仅当你的下一步动作依赖该结果时使用"
            "（超过约 2 分钟会自动转后台）。"
            "one_shot（默认 false）：一次性任务，完成后不可再用 send_message 追加。"
        ),
    )
    check = StructuredTool.from_function(
        func=check_task,
        coroutine=acheck_task,
        name="check_task",
        description="查询后台任务状态并收取结果（completed 时返回最终小结）。",
    )
    cancel = StructuredTool.from_function(
        func=cancel_task,
        coroutine=acancel_task,
        name="cancel_task",
        description="取消一个后台任务（不再需要其结果时使用）。",
    )
    followup_tool = StructuredTool.from_function(
        func=send_message,
        coroutine=asend_message,
        name="send_message",
        description=(
            "向子任务追加一条消息，作为它的新一轮执行（子 Agent 带全部历史接续推理）："
            "运行中任务在当前轮结束后执行该消息；已完成任务立即续跑并更新结果。"
            "适用于方向调整、补充要求或继续追问。一次性任务（one_shot）不支持。"
        ),
    )
    listing = StructuredTool.from_function(
        func=list_tasks,
        coroutine=alist_tasks,
        name="list_tasks",
        description="列出当前会话所有后台任务及状态。",
    )
    logger.info("background task tools ready session_id={}", session_id)
    return [start, check, cancel, followup_tool, listing]


__all__ = ["build_background_task_tools"]
