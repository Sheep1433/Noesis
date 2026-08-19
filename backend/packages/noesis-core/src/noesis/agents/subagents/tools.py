"""后台子 Agent 的模型侧工具（start / check / cancel / list）。

工具语义对齐 deepagents AsyncSubAgentMiddleware：start 立即返回 task_id，
模型可继续其他工作，稍后 check 收结果。session/user 在装配时闭包捕获。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BgTaskStatus,
)
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
    agent: Any,
    executor: BackgroundSubagentExecutor,
    session_id: str,
    user_id: str,
) -> list[StructuredTool]:
    """构造绑定到当前会话的后台任务工具集。

    ``agent`` 为编译好的 task-worker runnable（带 checkpointer），由
    ``compile_task_worker`` 产出，同一实例按不同 thread_id 复用。
    """

    def start_task(description: str) -> str:
        try:
            task_id = executor.start(
                agent=agent, description=description,
                session_id=session_id, user_id=user_id,
            )
        except ValueError as exc:
            return f"启动失败：{exc}"
        return (
            f"后台任务已启动：{task_id}\n"
            "无需等待——可继续其他工作，之后用 check_task 收结果。"
        )

    async def astart_task(description: str) -> str:
        return start_task(description)

    def check_task(task_id: str) -> str:
        task = executor.get(task_id)
        if task is None:
            # 已终态的历史任务从注册表查询不到时给出可诊断的提示
            return f"{task_id} 不存在（可能是进程重启前启动的旧任务）"
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
            "启动一个后台子 Agent 执行较重的独立子任务（多轮检索/调研/长命令）。"
            "立即返回 task_id，不阻塞当前工作；之后用 check_task 收结果。"
            "description 写清子目标、约束与期望输出格式。"
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
    listing = StructuredTool.from_function(
        func=list_tasks,
        coroutine=alist_tasks,
        name="list_tasks",
        description="列出当前会话所有后台任务及状态。",
    )
    logger.info("background task tools ready session_id={}", session_id)
    return [start, check, cancel, listing]


__all__ = ["build_background_task_tools"]
