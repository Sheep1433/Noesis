"""后台子 Agent 的模型侧工具（start / check / cancel / send / list）。

单工具同异步：``start_task`` 的 ``run_in_background``（默认 true）由模型
按依赖选择——后台立即返回 task_id；前台等待终态并把结果作为工具返回值，
超过 ``foreground_max_wait_seconds`` 自动转后台（同步转异步）。所有后台
subagent 都允许通过 ``send_message`` 继续对话；后台 shell 命令另行处理。
session/user 在装配时闭包捕获。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BgTaskStatus,
)
from noesis.chat.event_mapping.retrieval import (
    format_sources_appendix,
    register_pending_sources,
)
from noesis.config.env import ModelConfig, SubagentConfig
from noesis.runtime.logging import logger

_CHECK_PENDING_HINT = {
    BgTaskStatus.QUEUED: "排队中",
    BgTaskStatus.RUNNING: "仍在运行",
    BgTaskStatus.AWAITING_APPROVAL: "等待用户审批",
}


class _StartTaskArgs(BaseModel):
    description: str = Field(..., description="子任务的简短标题（10-20 字，用于任务卡与会话标题展示）")
    prompt: str = Field("", description="子 Agent 要执行的完整任务指令：子目标、约束、期望输出格式")
    run_in_background: bool = Field(True, description="是否立即返回并在后台执行")


class _ToolCallAwareStructuredTool(StructuredTool):
    """保留公开 schema，同时把模型真实 tool_call_id 注入实现函数。"""

    def _to_args_and_kwargs(self, tool_input: Any, tool_call_id: str | None):
        args, kwargs = super()._to_args_and_kwargs(tool_input, tool_call_id)
        kwargs["tool_call_id"] = tool_call_id or ""
        return args, kwargs


def _format_task(task: dict[str, Any], *, output_budget: int | None = None) -> str:
    """任务状态文本：终态携带结果/部分产出（受 output_budget 截断），stopping 提示进行中。"""
    public_id = str(task.get("child_session_id") or task.get("task_id") or "")
    status = task["status"]

    def _bounded(text: str | None) -> str:
        if not text:
            return ""
        if output_budget and len(text) > output_budget:
            return f"{text[:output_budget]}…（已截断，全文见 task.result）"
        return text

    if status == BgTaskStatus.COMPLETED.value:
        return f"[{public_id}] completed：\n{task.get('result') or '(无结果文本)'}"
    if status == BgTaskStatus.STOPPING.value:
        return f"[{public_id}] 正在停止（当前步骤完成后退出，稍后再查收部分产出）"
    # 进行中（queued / running / awaiting_approval）：状态提示，无产出可带
    pending_status = BgTaskStatus(status)
    if pending_status in _CHECK_PENDING_HINT:
        hint = _CHECK_PENDING_HINT[pending_status]
        return f"[{public_id}] {hint}（description: {task['description']}）"
    # cancelled / failed / timed_out：非正常终态统一「原因 + 部分产出」形态
    # （超时走协作路径时产出在 result，error 只有原因；取消同理）
    partial = task.get("result")
    if status == BgTaskStatus.CANCELLED.value:
        head = f"[{public_id}] cancelled（{task.get('stop_reason') or 'cancelled'}）" if partial else f"[{public_id}] cancelled"
    else:
        head = f"[{public_id}] {status}：{task.get('error') or ''}"
    if partial:
        return f"{head}\n{_bounded(str(partial))}"
    return head


def build_background_task_tools(
    *,
    worker_factory: Callable[[], Any],
    executor: BackgroundSubagentExecutor,
    session_id: str,
    user_id: str,
    create_child_session: Callable[[str, str, str | None], Awaitable[str | dict[str, Any]]] | None = None,
    delete_child_session: Callable[[str], Awaitable[None]] | None = None,
    fail_child_run: Callable[[str, str], Awaitable[None]] | None = None,
    create_followup_run: Callable[[str, str, str | None], Awaitable[dict[str, Any]]] | None = None,
    model_id: str | None = None,
) -> list[StructuredTool]:
    """构造绑定到当前会话的后台任务工具集。

    ``worker_factory`` 在隔离 loop 内惰性调用（async 可等待），产出带
    checkpointer 的 task-worker runnable；LLM 客户端与连接池随之绑定
    隔离 loop。
    """

    async def astart_task(
        description: str,
        prompt: str = "",
        run_in_background: bool = True,
        tool_call_id: str = "",
    ) -> str:
        # description = 简短标题；prompt = 完整任务指令（缺省回退 description，兼容旧调用）
        task_text = prompt.strip() or description
        launch = (
            await create_child_session(description, task_text, tool_call_id)
            if create_child_session else None
        )
        if isinstance(launch, dict):
            child_session_id = str(launch.get("child_session_id") or "") or None
            run_id = str(launch.get("run_id") or "") or None
            assistant_message_id = str(launch.get("assistant_message_id") or "") or None
            created_by_tool_call_id = str(launch.get("created_by_tool_call_id") or "") or None
        else:
            child_session_id = launch
            run_id = None
            assistant_message_id = None
            created_by_tool_call_id = None
        try:
            task_id = executor.start(
                worker_factory=worker_factory, description=description,
                prompt=task_text,
                session_id=session_id, user_id=user_id,
                child_session_id=child_session_id,
                created_by_tool_call_id=created_by_tool_call_id,
                run_id=run_id,
                assistant_message_id=assistant_message_id,
                followup_factory=create_followup_run,
                model_id=model_id,
            )
        except ValueError as exc:
            # 启动被拒（并发超限已改为排队，此分支仅剩其他启动失败）：
            # run 必须先置 ERROR 再软删会话，否则残留 QUEUED run 会被
            # dispatcher claim 并以 RUN_START_FAILED 失败。
            if run_id and fail_child_run is not None:
                try:
                    await fail_child_run(run_id, str(exc))
                except Exception:  # noqa: BLE001
                    logger.exception("标记被拒子 Agent run 失败 run_id={}", run_id)
            if child_session_id and delete_child_session is not None:
                try:
                    await delete_child_session(child_session_id)
                except Exception:  # noqa: BLE001
                    logger.exception("清理未启动的子 Agent 会话失败 child_session_id={}", child_session_id)
            return f"启动失败：{exc}"
        if run_in_background:
            public_id = child_session_id or task_id
            return (
                f"子 Agent 已启动：{public_id}\n"
                "无需等待——可继续其他工作，之后用 check_task 收结果。"
            )
        # 前台等待：执行仍走后台路径，跨 loop 等待终态；
        # shield 保证超时取消不波及底层任务（自动转后台）
        future = BackgroundSubagentExecutor.get_future(task_id)
        if future is None:
            return f"子 Agent 已启动：{child_session_id or task_id}（前台等待不可用，稍后 check_task 收结果）"
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=SubagentConfig.foreground_max_wait_seconds,
            )
        except asyncio.TimeoutError:
            return (
                f"任务运行超过 {int(SubagentConfig.foreground_max_wait_seconds)}s，已自动转为后台：{child_session_id or task_id}\n"
                "可继续其他工作，之后用 check_task 收结果。"
            )
        task = BackgroundSubagentExecutor.get(task_id) or {"task_id": task_id, "status": "unknown"}
        public_id = str(task.get("child_session_id") or child_session_id or task_id)
        status = task.get("status")
        if status == BgTaskStatus.COMPLETED.value:
            # Keep the task id in every foreground terminal response so the
            # client can link the inline card to its persisted conversation.
            return f"任务完成（{public_id}）：\n{task.get('result') or '(无结果文本)'}"
        if status == BgTaskStatus.AWAITING_APPROVAL.value:
            return (
                f"任务等待用户审批（{public_id}）。审批通过后任务继续后台运行，"
                "稍后用 check_task 收结果。"
            )
        if status in (BgTaskStatus.FAILED.value, BgTaskStatus.TIMED_OUT.value):
            return f"任务{status}（{task_id}）：{task.get('error') or ''}"
        return _format_task(task)

    def start_task(
        description: str,
        prompt: str = "",
        run_in_background: bool = True,
        tool_call_id: str = "",
    ) -> str:
        # 同步入口：langgraph 工具实际走 coroutine；保留同步回退
        if run_in_background:
            if create_child_session is not None:
                return "启动失败：子 Agent 必须在异步执行环境中创建会话"
            try:
                task_id = executor.start(
                    worker_factory=worker_factory, description=description,
                    prompt=prompt.strip() or description,
                    session_id=session_id, user_id=user_id,
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
            return f"{task_id} 不存在（可用 list_tasks 查看当前任务与完整 task_id）"
        if task["session_id"] != session_id:
            return f"{task_id} 不属于当前会话"
        text = _format_task(task, output_budget=ModelConfig.tool_output_max_chars)
        # 终态小结后附去重来源清单段（模型侧纯增益，受附录上界约束）；
        # 结构化清单同步进入跨边界登记，主 run 桥接层 finish 时落为
        # 带 origin（该子 Agent 任务）的 retrieval parts。
        sources = BackgroundSubagentExecutor.sources_of(task_id)
        if sources:
            register_pending_sources(
                session_id, str(task.get("description") or ""), sources,
            )
            appendix = format_sources_appendix(sources)
            if appendix:
                text = f"{text}\n\n{appendix}"
        return text

    async def acheck_task(task_id: str) -> str:
        return check_task(task_id)

    def cancel_task(task_id: str) -> str:
        try:
            task = executor.cancel(task_id)
        except ValueError as exc:
            return f"取消失败：{exc}"
        if task["status"] == BgTaskStatus.STOPPING.value:
            return (
                f"已请求停止：{task['task_id']}（协作式——当前步骤完成后停止，"
                "可用 check_task 收取中止前的部分产出）"
            )
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
        return "\n".join(
            _format_task(t, output_budget=ModelConfig.tool_output_max_chars) for t in tasks
        )

    async def alist_tasks() -> str:
        return list_tasks()

    start = _ToolCallAwareStructuredTool.from_function(
        func=start_task,
        coroutine=astart_task,
        args_schema=_StartTaskArgs,
        name="start_task",
        description=(
            "启动一个子 Agent 执行较重的独立子任务（多轮检索/调研/长命令）。"
            "description：子任务的简短标题（10-20 字，用于任务卡与会话标题）。"
            "prompt：完整任务指令——写清子目标、约束与期望输出格式。"
            "run_in_background（默认 true）：立即返回 task_id，可继续其他工作，之后用 check_task 收结果；"
            "false：前台等待结果直接返回——仅当你的下一步动作依赖该结果时使用"
            "（超过约 2 分钟会自动转后台）。"
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
            "适用于方向调整、补充要求或继续追问。"
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
