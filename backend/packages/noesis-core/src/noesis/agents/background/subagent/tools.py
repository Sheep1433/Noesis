"""AsyncSubagentToolsMiddleware — subagent 工具面（start/check/update/cancel/list）。

参照 deepagents 0.6.12 ``AsyncSubAgentMiddleware`` 的结构与语义自研（文件头
标注参照来源与版本）：工具命名（start/check/update/cancel/list _async_task）、
``Command`` 写入机制、类型清单注入与上游同构；远程适配层（url/headers/
ClientCache/LangGraph SDK 依赖）不迁移，工具直调运行时。在上游基础上的增强：
前台等待（超时自动转后台）、description/prompt 双字段、check 来源清单附录。

任务身份写入 graph state 的投影契约在 ``background/task_state.py``（与
shell 工具面共用）；本模块只持有 subagent 专属的工具与前台等待逻辑，
system prompt 追加角色类型清单供模型选择 subagent_type。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from noesis.agents.background.executor import (
    BackgroundTaskExecutor,
    BgTaskStatus,
)
from noesis.agents.background.subagent.roles import SubagentRegistry
from noesis.agents.background.task_state import (
    SubagentTasksState,
    build_async_task_identity,
)
from noesis.chat.event_mapping.retrieval import (
    format_sources_appendix,
    register_pending_sources,
)
from noesis.config.env import ModelConfig
from noesis.runtime.logging import logger

# 前台等待上限（工程常量，不进配置）：超过即自动转后台。评测 CLI 子进程
# 经 SUBAGENT_FOREGROUND_MAX_WAIT_SECONDS env 覆盖（单回合评测无通知回合）
FOREGROUND_MAX_WAIT_SECONDS = float(
    os.environ.get("SUBAGENT_FOREGROUND_MAX_WAIT_SECONDS", "600")
)

_CHECK_PENDING_HINT = {
    BgTaskStatus.QUEUED: "排队中",
    BgTaskStatus.RUNNING: "仍在运行",
}

_TYPES_PROMPT_HEADER = "可用的子 Agent 角色类型（start_async_task 的 subagent_type）："


class _StartTaskArgs(BaseModel):
    description: str = Field(..., description="子任务的简短标题（10-20 字，用于任务卡与会话标题展示）")
    prompt: str = Field("", description="子 Agent 要执行的完整任务指令：子目标、约束、期望输出格式")
    subagent_type: str = Field(..., description="子 Agent 角色类型（可用值见系统提示的类型清单；按任务性质选择）")
    run_in_background: bool = Field(
        False,
        description=(
            "默认 false：前台等待结果直接返回，超过约 10 分钟自动转后台（之后用 check_async_task 收结果）；"
            "仅当任务预计远超数分钟、或要与其它子任务并行时才传 true（立即返回任务 id）"
        ),
    )


class _ToolCallAwareStructuredTool(StructuredTool):
    """保留公开 schema，同时把模型真实 tool_call_id 注入实现函数。"""

    def _to_args_and_kwargs(self, tool_input: Any, tool_call_id: str | None):
        args, kwargs = super()._to_args_and_kwargs(tool_input, tool_call_id)
        kwargs["tool_call_id"] = tool_call_id or ""
        return args, kwargs


def _format_task(task: dict[str, Any], *, output_budget: int | None = None) -> str:
    """任务状态文本：终态携带结果/部分产出（受 output_budget 截断）。"""
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
    pending_status = BgTaskStatus(status)
    if pending_status in _CHECK_PENDING_HINT:
        hint = _CHECK_PENDING_HINT[pending_status]
        text = f"[{public_id}] {hint}（description: {task['description']}）"
        # shell 任务的运行中输出尾部快照（流式 flush 到 output_tail）
        tail = task.get("output_tail")
        if tail:
            text += f"\n\n[运行中输出尾部]\n{tail}"
        return text
    partial = task.get("result")
    if status == BgTaskStatus.CANCELLED.value:
        head = f"[{public_id}] cancelled（{task.get('stop_reason') or 'cancelled'}）" if partial else f"[{public_id}] cancelled"
    else:
        head = f"[{public_id}] {status}：{task.get('error') or ''}"
    if partial:
        return f"{head}\n{_bounded(str(partial))}"
    return head


def _append_to_system_message(system_message, text: str):
    """向 system message 追加段落（无 system message 时新建一条）。"""
    if system_message is None:
        return SystemMessage(content=text)
    content = system_message.content
    if isinstance(content, str):
        new_content = f"{content}\n\n{text}"
    else:
        new_content = [*content, {"type": "text", "text": f"\n\n{text}"}]
    return system_message.model_copy(update={"content": new_content})


class AsyncSubagentToolsMiddleware(
    AgentMiddleware[SubagentTasksState, ContextT, ResponseT],
):
    """子 Agent 工具面 + 任务身份 state（仅主 Agent 栈挂载）。"""

    state_schema = SubagentTasksState

    def __init__(
        self,
        *,
        registry: SubagentRegistry,
        executor: BackgroundTaskExecutor,
        session_id: str,
        user_id: str,
        create_child_session: Callable[
            [str, str | None, str, str, str | None],
            Awaitable[str | dict[str, Any]],
        ] | None = None,
        delete_child_session: Callable[[str], Awaitable[None]] | None = None,
        fail_child_run: Callable[[str, str], Awaitable[None]] | None = None,
        create_turn_run: Callable[[str, str, str | None], Awaitable[dict[str, Any]]] | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._executor = executor
        self._session_id = session_id
        self._user_id = user_id
        self._create_child_session = create_child_session
        self._delete_child_session = delete_child_session
        self._fail_child_run = fail_child_run
        self._create_turn_run = create_turn_run
        self._model_id = model_id
        self.tools = self._build_tools()
        self.system_prompt: str | None = (
            f"{_TYPES_PROMPT_HEADER}\n{registry.types_prompt()}"
        )

    # -- prompt 注入 --------------------------------------------------

    def _with_types_prompt(self, system_message):
        if self.system_prompt is None:
            return system_message
        return _append_to_system_message(system_message, self.system_prompt)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        new_system = self._with_types_prompt(request.system_message)
        if new_system is not request.system_message:
            request = request.override(system_message=new_system)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT], Awaitable[ModelResponse[ResponseT]]]],
    ) -> ModelResponse[ResponseT]:
        new_system = self._with_types_prompt(request.system_message)
        if new_system is not request.system_message:
            request = request.override(system_message=new_system)
        return await handler(request)

    # -- 工具面 -------------------------------------------------------

    def _build_tools(self) -> list[StructuredTool]:
        registry = self._registry
        executor = self._executor
        session_id = self._session_id
        user_id = self._user_id
        create_child_session = self._create_child_session
        delete_child_session = self._delete_child_session
        fail_child_run = self._fail_child_run
        create_turn_run = self._create_turn_run
        model_id = self._model_id

        async def astart_async_task(
            description: str,
            prompt: str = "",
            subagent_type: str = "",
            run_in_background: bool = False,
            tool_call_id: str = "",
        ):
            # description = 简短标题；prompt = 完整任务指令（缺省回退 description，兼容旧调用）
            role = registry.get(subagent_type)
            if role is None:
                available = "、".join(registry.names())
                return (
                    f"启动失败：未知子 Agent 类型 {subagent_type or '(空)'}。"
                    f"可用类型：{available}"
                )
            effective_model = registry.effective_model(subagent_type, model_id)
            task_text = prompt.strip() or description
            launch = (
                await create_child_session(
                    description, task_text, tool_call_id, subagent_type, effective_model,
                )
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
                    worker_factory=role.worker_factory, description=description,
                    prompt=task_text,
                    session_id=session_id, user_id=user_id,
                    child_session_id=child_session_id,
                    created_by_tool_call_id=created_by_tool_call_id,
                    run_id=run_id,
                    assistant_message_id=assistant_message_id,
                    turn_factory=create_turn_run,
                    model_id=effective_model,
                    subagent_type=subagent_type,
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
                text = (
                    f"子 Agent 已启动：{public_id}\n"
                    "无需等待——可继续其他工作，之后用 check_async_task 收结果。"
                )
                return _command_with_identity(tool_call_id, text, {
                    "task_id": task_id, "child_session_id": public_id,
                    "subagent_type": subagent_type, "description": description,
                    "status": BgTaskStatus.RUNNING.value, "run_id": run_id or "",
                })
            # 前台等待：执行仍走后台路径，跨 loop 等待终态；
            # shield 保证超时取消不波及底层任务（自动转后台）
            future = BackgroundTaskExecutor.get_future(task_id)
            if future is None:
                text = f"子 Agent 已启动：{child_session_id or task_id}（前台等待不可用，稍后 check_async_task 收结果）"
                return _command_with_identity(tool_call_id, text, {
                    "task_id": task_id, "child_session_id": child_session_id or task_id,
                    "subagent_type": subagent_type, "description": description,
                    "status": BgTaskStatus.RUNNING.value, "run_id": run_id or "",
                })
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(future)),
                    timeout=FOREGROUND_MAX_WAIT_SECONDS,
                )
            except asyncio.TimeoutError:
                text = (
                    f"任务运行超过 {int(FOREGROUND_MAX_WAIT_SECONDS)}s，已自动转为后台：{child_session_id or task_id}\n"
                    "可继续其他工作，之后用 check_async_task 收结果。"
                )
                return _command_with_identity(tool_call_id, text, {
                    "task_id": task_id, "child_session_id": child_session_id or task_id,
                    "subagent_type": subagent_type, "description": description,
                    "status": BgTaskStatus.RUNNING.value, "run_id": run_id or "",
                })
            except asyncio.CancelledError:
                # 子 Agent 任务自身被取消（硬超时/回收）会穿透 shield 炸掉
                # 主运行——曾三次在评测中复现（Node 'tools' raised
                # CancelledError）。内层取消降级为可收部分结果的提示；
                # 外部取消（主运行停止）必须原样传播。
                if future.done() and future.cancelled():
                    text = (
                        f"子 Agent 任务已终止（超时或取消）：{child_session_id or task_id}\n"
                        "部分产出仍在回收中，可用 check_async_task 收取。"
                    )
                    return _command_with_identity(tool_call_id, text, {
                        "task_id": task_id, "child_session_id": child_session_id or task_id,
                        "subagent_type": subagent_type, "description": description,
                        "status": BgTaskStatus.TIMED_OUT.value, "run_id": run_id or "",
                    })
                raise
            task = BackgroundTaskExecutor.get(task_id) or {"task_id": task_id, "status": "unknown"}
            public_id = str(task.get("child_session_id") or child_session_id or task_id)
            status = task.get("status")
            if status == BgTaskStatus.COMPLETED.value:
                # Keep the task id in every foreground terminal response so the
                # client can link the inline card to its persisted conversation.
                text = f"任务完成（{public_id}）：\n{task.get('result') or '(无结果文本)'}"
                return _command_with_identity(tool_call_id, text, task)
            if status in (BgTaskStatus.FAILED.value, BgTaskStatus.TIMED_OUT.value):
                text = f"任务{status}（{public_id}）：{task.get('error') or ''}"
                return _command_with_identity(tool_call_id, text, task)
            # 前台等待内协作停止完成（cancelled 携带部分产出）
            return _command_with_identity(tool_call_id, _format_task(task), task)

        async def acheck_async_task(task_id: str) -> str:
            # 热集内读内存快照，miss 读 DB 投影（回收后任务仍可答，不误报不存在）
            task = await executor.check_with_fallback(task_id)
            if task is None:
                return f"{task_id} 不存在（可用 list_async_tasks 查看当前任务与完整 task_id）"
            if task["session_id"] != session_id:
                return f"{task_id} 不属于当前会话"
            text = _format_task(task, output_budget=ModelConfig.tool_output_max_chars)
            if task.get("undelivered_messages"):
                text += (
                    f"\n\n[提示] 该任务有 {task['undelivered_messages']} 条重启前追加的指示未执行"
                    "（进程重启），是否重新下达请告知用户。"
                )
            if task.get("terminal_persist_exhausted"):
                text += "\n\n[提示] 该任务终态落库失败（数据可能不完整）。"
            # 终态小结后附去重来源清单段（模型侧纯增益，受附录上界约束）；
            # 结构化清单同步进入跨边界登记，主 run 桥接层 finish 时落为
            # 带 origin（该子 Agent 任务）的 retrieval parts。
            sources = task.get("retrieval_sources") or []
            if sources:
                register_pending_sources(
                    session_id, str(task.get("description") or ""), sources,
                )
                appendix = format_sources_appendix(sources)
                if appendix:
                    text = f"{text}\n\n{appendix}"
            return text

        async def acancel_async_task(task_id: str) -> str:
            try:
                task = await executor.cancel_with_fallback(task_id)
            except ValueError as exc:
                return f"取消失败：{exc}"
            if task.get("kind") == "subagent" or task["status"] == "timed_out":
                # 协作路径（running 受理）/ 超时：执行侧终态处理异步回收部分产出
                return (
                    f"已取消：{task['task_id']}（部分产出在后台回收中，"
                    "稍后可用 check_async_task 查收）"
                )
            return f"已取消：{task['task_id']}"

        async def asend_message(
            task_id: str, message: str, tool_call_id: str = "",
        ) -> str | Command:
            """向子任务追加一轮执行（与用户 HTTP 同走命令受理，单一路径）。"""
            from noesis.agents.background.ports import SubagentSessionPort

            try:
                task = await SubagentSessionPort.accept_message(
                    task_ref=task_id,
                    user_id=user_id,
                    message=message,
                )
            except ValueError as exc:
                return f"发送失败：{exc}"
            text = (
                f"消息已提交：{task_id}（作为子任务的新一轮执行，"
                "运行中任务在当前轮结束后生效；已完成任务立即续跑）"
            )
            return _command_with_identity(tool_call_id, text, task)

        async def alist_async_tasks() -> str:
            tasks = await executor.list_with_fallback(session_id)
            if not tasks:
                return "当前会话没有后台任务"
            return "\n".join(
                _format_task(t, output_budget=ModelConfig.tool_output_max_chars) for t in tasks
            )

        start = _ToolCallAwareStructuredTool.from_function(
            func=None,
            coroutine=astart_async_task,
            args_schema=_StartTaskArgs,
            name="start_async_task",
            description=(
                "启动一个子 Agent 执行较重的独立子任务（多轮检索/调研/长命令）。"
                "description：子任务的简短标题（10-20 字，用于任务卡与会话标题）。"
                "prompt：完整任务指令——写清子目标、约束与期望输出格式。"
                "subagent_type（必填）：子 Agent 角色类型，按任务性质从系统提示的类型清单中选择。"
                "run_in_background（默认 false）：前台等待，结果直接随本次调用返回；"
                "超过约 10 分钟自动转后台，之后用 check_async_task 收结果。"
                "仅当子任务预计远超数分钟、或要与其它子任务并行推进时才显式传 true（立即返回任务 id）。"
            ),
        )
        check = StructuredTool.from_function(
            coroutine=acheck_async_task,
            name="check_async_task",
            description=(
                "查询后台任务状态并收取结果（completed 时返回最终小结；"
                "shell 命令运行中会附最新输出尾部，可中途查看进度）。"
                "由任务终态的 [系统通知] 驱动调用；启动后不要反复轮询——"
                "确需中途了解进度用 list_async_tasks 或对 shell 任务查输出尾部。"
            ),
        )
        cancel = StructuredTool.from_function(
            coroutine=acancel_async_task,
            name="cancel_async_task",
            description="取消一个后台任务（不再需要其结果时使用）。",
        )
        update = _ToolCallAwareStructuredTool.from_function(
            func=None,
            coroutine=asend_message,
            name="send_message",
            description=(
                "向子任务追加一条消息，作为它的新一轮执行（子 Agent 带全部历史接续推理）："
                "运行中任务在当前轮结束后执行该消息；已完成任务立即续跑并更新结果。"
                "适用于方向调整、补充要求或继续追问。"
            ),
        )
        listing = StructuredTool.from_function(
            coroutine=alist_async_tasks,
            name="list_async_tasks",
            description="列出当前会话所有后台任务及状态。",
        )
        # 与 factory._annotate_builtin_tools 同款标注：middleware 自带工具
        # 不经 tools= 通道，需在此补 provider key，统计归因才不退化为 unknown
        tools = [start, check, cancel, update, listing]
        for tool in tools:
            metadata = getattr(tool, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = {}
                tool.metadata = metadata
            metadata.setdefault("noesis_provider_key", "builtin")
        logger.info("async subagent tools middleware ready session_id={}", session_id)
        return tools


def _command_with_identity(
    tool_call_id: str,
    text: str,
    task: dict[str, Any],
) -> Command:
    """以 Command 返回工具文本，同时把任务身份写入 ``async_tasks`` state。"""
    identity = build_async_task_identity(task)
    return Command(update={
        "messages": [ToolMessage(text, tool_call_id=tool_call_id)],
        "async_tasks": {identity["task_id"]: identity},
    })



__all__ = [
    "AsyncSubagentToolsMiddleware",
    "FOREGROUND_MAX_WAIT_SECONDS",
]
