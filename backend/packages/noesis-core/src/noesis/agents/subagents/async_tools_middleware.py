"""AsyncSubagentToolsMiddleware — 子 Agent 工具面 + 任务身份 graph state。

参照 deepagents 0.6.12 ``AsyncSubAgentMiddleware`` 的结构与语义自研（文件头
标注参照来源与版本）：工具命名（start/check/update/cancel/list _async_task）、
``AsyncTask`` 状态结构、``Command`` 写入机制、类型清单注入与上游同构；远程
适配层（url/headers/ClientCache/LangGraph SDK 依赖）不迁移，工具直调运行时。
在上游基础上的增强：前台等待（超时自动转后台）、description/prompt 双字段、
check 来源清单附录。

- **任务身份**：启动成功经 ``Command`` 把身份写入 graph state ``async_tasks``，
  随 checkpoint 持久化、免疫上下文压缩。state 是投影——任务状态与结果的
  权威来源永远是运行时注册表（miss 落 DB），``check_async_task`` 不信快照；
- **prompt 注入**：system prompt 追加角色类型清单，供模型选择 subagent_type。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Annotated, Any, Awaitable, Callable, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from noesis.agents.subagents.executor import (
    BackgroundTaskExecutor,
    BgTaskStatus,
)
from noesis.agents.subagents.registry import SubagentRegistry
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

# 状态键归本中间件所有；不进 stack 的 subagent 隔离携带集合——worker 无
# 后台任务工具，父会话任务清单不向子 Agent checkpoint 传递
PRIVATE_STATE_KEYS: tuple[str, ...] = ("async_tasks",)

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


class AsyncTask(TypedDict):
    """任务身份投影（上游同构字段 + description），压缩后模型仍可恢复任务清单。

    与上游的对应：thread_id = 子会话公开身份（child session），agent_name =
    角色类型（后台命令为 shell）。时间戳为写入时刻值，不随后续操作刷新
    ——权威状态实时查运行时，state 只服务压缩后的任务清单重建。
    """

    task_id: str
    agent_name: str
    thread_id: str
    run_id: str
    status: str
    description: str
    created_at: str
    last_checked_at: str
    last_updated_at: str


def _merge_async_tasks(
    existing: dict[str, AsyncTask] | None,
    update: dict[str, AsyncTask],
) -> dict[str, AsyncTask]:
    """按 task_id 合并；终态条目保留（压缩后已收结果的任务仍可追溯）。"""
    merged = dict(existing or {})
    merged.update(update)
    return merged


class SubagentTasksState(AgentState):
    async_tasks: NotRequired[Annotated[dict[str, AsyncTask], _merge_async_tasks]]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_async_task_identity(task: dict[str, Any]) -> AsyncTask:
    """从运行时任务快照构造 state 身份条目（start/update/execute 后台分支共用）。

    时间戳为写入时刻值：check/update 不回写 state（权威状态实时查运行时），
    字段保留上游 AsyncTask 形状。
    """
    now = _now_iso()
    return AsyncTask(
        task_id=str(task["task_id"]),
        agent_name=str(task.get("subagent_type") or "shell"),
        thread_id=str(task.get("child_session_id") or task["task_id"]),
        run_id=str(task.get("run_id") or ""),
        status=str(task.get("status") or ""),
        description=str(task.get("description") or ""),
        created_at=now,
        last_checked_at=now,
        last_updated_at=now,
    )


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
        return f"[{public_id}] {hint}（description: {task['description']}）"
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
        create_followup_run: Callable[[str, str, str | None], Awaitable[dict[str, Any]]] | None = None,
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
        self._create_followup_run = create_followup_run
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
        create_followup_run = self._create_followup_run
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
                    followup_factory=create_followup_run,
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
            task = BackgroundTaskExecutor.get(task_id) or {"task_id": task_id, "status": "unknown"}
            public_id = str(task.get("child_session_id") or child_session_id or task_id)
            status = task.get("status")
            if status == BgTaskStatus.COMPLETED.value:
                # Keep the task id in every foreground terminal response so the
                # client can link the inline card to its persisted conversation.
                text = f"任务完成（{public_id}）：\n{task.get('result') or '(无结果文本)'}"
                return _command_with_identity(tool_call_id, text, task)
            if status in (BgTaskStatus.FAILED.value, BgTaskStatus.TIMED_OUT.value):
                return f"任务{status}（{task_id}）：{task.get('error') or ''}"
            return _format_task(task)

        async def acheck_async_task(task_id: str) -> str:
            task = executor.get(task_id)
            if task is None:
                # 内存 miss 时 get 已查持久层；到这里说明任务 ID 确实未知
                return f"{task_id} 不存在（可用 list_async_tasks 查看当前任务与完整 task_id）"
            if task["session_id"] != session_id:
                return f"{task_id} 不属于当前会话"
            text = _format_task(task, output_budget=ModelConfig.tool_output_max_chars)
            # 终态小结后附去重来源清单段（模型侧纯增益，受附录上界约束）；
            # 结构化清单同步进入跨边界登记，主 run 桥接层 finish 时落为
            # 带 origin（该子 Agent 任务）的 retrieval parts。
            sources = BackgroundTaskExecutor.sources_of(task_id)
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
                task = executor.cancel(task_id)
            except ValueError as exc:
                return f"取消失败：{exc}"
            if task.get("kind") == "subagent" or task["status"] == "timed_out":
                # 协作路径（running 受理）/ 超时：执行侧收口异步回收部分产出
                return (
                    f"已取消：{task['task_id']}（部分产出在后台回收中，"
                    "稍后可用 check_async_task 查收）"
                )
            return f"已取消：{task['task_id']}"

        async def aupdate_async_task(
            task_id: str, message: str, tool_call_id: str = "",
        ) -> str | Command:
            """向子任务追加一轮执行（上游 update_async_task 语义 + 本地 followup 管线）。"""
            try:
                task = await executor.deliver_followup(task_id, message)
            except ValueError as exc:
                return f"发送失败：{exc}"
            text = (
                f"消息已提交：{task_id}（作为子任务的新一轮执行，"
                "运行中任务在当前轮结束后生效；已完成任务立即续跑）"
            )
            return _command_with_identity(tool_call_id, text, task)

        async def alist_async_tasks() -> str:
            tasks = executor.list_for_session(session_id)
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
                "查询后台任务状态并收取结果（completed 时返回最终小结）。"
                "由任务终态的 [系统通知] 驱动调用；启动后不要反复轮询——"
                "确需中途了解进度用 list_async_tasks。"
            ),
        )
        cancel = StructuredTool.from_function(
            coroutine=acancel_async_task,
            name="cancel_async_task",
            description="取消一个后台任务（不再需要其结果时使用）。",
        )
        update = _ToolCallAwareStructuredTool.from_function(
            func=None,
            coroutine=aupdate_async_task,
            name="update_async_task",
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
    "AsyncTask",
    "PRIVATE_STATE_KEYS",
    "SubagentTasksState",
    "build_async_task_identity",
]
