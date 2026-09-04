"""NoesisSubagentMiddleware — 子 Agent 工具面 + 任务身份 graph state。

SuperAgent 的子 Agent 能力以 middleware 形态挂载（经 ``middleware=`` 注入，
task-worker 栈不挂载）：

- **工具面**：start / check / cancel / send_message / list_tasks 五工具，
  ``start_task`` 按角色注册表分发（``subagent_type`` 必填）；
- **任务身份**：启动成功经 ``Command`` 把身份（task_id / child_session_id /
  subagent_type / description / 状态快照）写入 graph state ``bg_tasks``，
  随 checkpoint 持久化、免疫上下文压缩。state 是投影——任务状态与结果的
  权威来源永远是执行器注册表（miss 落 DB），``check_task`` 不信快照；
- **prompt 注入**：system prompt 追加角色类型清单，供模型选择 subagent_type。
"""

from __future__ import annotations

import asyncio
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
from noesis.config.env import ModelConfig, SubagentConfig
from noesis.runtime.logging import logger


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

# 状态键归本中间件所有；不进 stack 的 subagent 隔离携带集合——worker 无
# 后台任务工具，父会话任务清单不向子 Agent checkpoint 传递
PRIVATE_STATE_KEYS: tuple[str, ...] = ("bg_tasks",)

_CHECK_PENDING_HINT = {
    BgTaskStatus.QUEUED: "排队中",
    BgTaskStatus.RUNNING: "仍在运行",
    BgTaskStatus.AWAITING_APPROVAL: "等待用户审批",
}

_TYPES_PROMPT_HEADER = "可用的子 Agent 角色类型（start_task 的 subagent_type）："


class _StartTaskArgs(BaseModel):
    description: str = Field(..., description="子任务的简短标题（10-20 字，用于任务卡与会话标题展示）")
    prompt: str = Field("", description="子 Agent 要执行的完整任务指令：子目标、约束、期望输出格式")
    subagent_type: str = Field(..., description="子 Agent 角色类型（可用值见系统提示的类型清单；按任务性质选择）")
    run_in_background: bool = Field(
        False,
        description=(
            "默认 false：前台等待结果直接返回，超过约 2 分钟自动转后台（之后用 check_task 收结果）；"
            "仅当任务预计远超数分钟、或要与其它子任务并行时才传 true（立即返回 task_id）"
        ),
    )


class _ToolCallAwareStructuredTool(StructuredTool):
    """保留公开 schema，同时把模型真实 tool_call_id 注入实现函数。"""

    def _to_args_and_kwargs(self, tool_input: Any, tool_call_id: str | None):
        args, kwargs = super()._to_args_and_kwargs(tool_input, tool_call_id)
        kwargs["tool_call_id"] = tool_call_id or ""
        return args, kwargs


class BgTaskIdentity(TypedDict):
    """任务身份投影：压缩后模型仍可据此恢复任务清单。"""

    task_id: str
    child_session_id: str
    subagent_type: str
    description: str
    # 写入时的状态快照，仅供清单重建；权威状态实时查执行器
    last_status: str


def _merge_bg_tasks(
    existing: dict[str, BgTaskIdentity] | None,
    update: dict[str, BgTaskIdentity],
) -> dict[str, BgTaskIdentity]:
    """按 task_id 合并；终态条目保留（压缩后已收结果的任务仍可追溯）。"""
    merged = dict(existing or {})
    merged.update(update)
    return merged


class SubagentTasksState(AgentState):
    bg_tasks: NotRequired[Annotated[dict[str, BgTaskIdentity], _merge_bg_tasks]]


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


class NoesisSubagentMiddleware(
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

        async def astart_task(
            description: str,
            prompt: str = "",
            subagent_type: str = "",
            run_in_background: bool = True,
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
                    "无需等待——可继续其他工作，之后用 check_task 收结果。"
                )
                return _command_with_identity(tool_call_id, text, {
                    "task_id": task_id, "child_session_id": public_id,
                    "subagent_type": subagent_type, "description": description,
                    "status": BgTaskStatus.RUNNING.value,
                })
            # 前台等待：执行仍走后台路径，跨 loop 等待终态；
            # shield 保证超时取消不波及底层任务（自动转后台）
            future = BackgroundTaskExecutor.get_future(task_id)
            if future is None:
                text = f"子 Agent 已启动：{child_session_id or task_id}（前台等待不可用，稍后 check_task 收结果）"
                return _command_with_identity(tool_call_id, text, {
                    "task_id": task_id, "child_session_id": child_session_id or task_id,
                    "subagent_type": subagent_type, "description": description,
                    "status": BgTaskStatus.RUNNING.value,
                })
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(future)),
                    timeout=SubagentConfig.foreground_max_wait_seconds,
                )
            except asyncio.TimeoutError:
                text = (
                    f"任务运行超过 {int(SubagentConfig.foreground_max_wait_seconds)}s，已自动转为后台：{child_session_id or task_id}\n"
                    "可继续其他工作，之后用 check_task 收结果。"
                )
                return _command_with_identity(tool_call_id, text, {
                    "task_id": task_id, "child_session_id": child_session_id or task_id,
                    "subagent_type": subagent_type, "description": description,
                    "status": BgTaskStatus.RUNNING.value,
                })
            task = BackgroundTaskExecutor.get(task_id) or {"task_id": task_id, "status": "unknown"}
            public_id = str(task.get("child_session_id") or child_session_id or task_id)
            status = task.get("status")
            if status == BgTaskStatus.COMPLETED.value:
                # Keep the task id in every foreground terminal response so the
                # client can link the inline card to its persisted conversation.
                text = f"任务完成（{public_id}）：\n{task.get('result') or '(无结果文本)'}"
                return _command_with_identity(tool_call_id, text, task)
            if status == BgTaskStatus.AWAITING_APPROVAL.value:
                text = (
                    f"任务等待用户审批（{public_id}）。审批通过后任务继续后台运行，"
                    "稍后用 check_task 收结果。"
                )
                return _command_with_identity(tool_call_id, text, task)
            if status in (BgTaskStatus.FAILED.value, BgTaskStatus.TIMED_OUT.value):
                return f"任务{status}（{task_id}）：{task.get('error') or ''}"
            return _format_task(task)

        def start_task(
            description: str,
            prompt: str = "",
            subagent_type: str = "",
            run_in_background: bool = True,
            tool_call_id: str = "",
        ) -> str:
            # 同步入口：langgraph 工具实际走 coroutine；保留同步回退
            if run_in_background:
                role = registry.get(subagent_type)
                if role is None:
                    available = "、".join(registry.names())
                    return (
                        f"启动失败：未知子 Agent 类型 {subagent_type or '(空)'}。"
                        f"可用类型：{available}"
                    )
                if create_child_session is not None:
                    return "启动失败：子 Agent 必须在异步执行环境中创建会话"
                try:
                    task_id = executor.start(
                        worker_factory=role.worker_factory, description=description,
                        prompt=prompt.strip() or description,
                        session_id=session_id, user_id=user_id,
                        subagent_type=subagent_type,
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
            sources = BackgroundTaskExecutor.sources_of(task_id)
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
                "subagent_type（必填）：子 Agent 角色类型，按任务性质从系统提示的类型清单中选择。"
                "run_in_background（默认 false）：前台等待，结果直接随本次调用返回；"
                "超过约 2 分钟自动转后台，之后用 check_task 收结果。"
                "仅当子任务预计远超数分钟、或要与其它子任务并行推进时才显式传 true（立即返回 task_id）。"
            ),
        )
        check = StructuredTool.from_function(
            func=check_task,
            coroutine=acheck_task,
            name="check_task",
            description=(
                "查询后台任务状态并收取结果（completed 时返回最终小结）。"
                "由任务终态的 [系统通知] 驱动调用；启动后不要反复轮询——"
                "确需中途了解进度用 list_tasks。"
            ),
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
        # 与 factory._annotate_builtin_tools 同款标注：middleware 自带工具
        # 不经 tools= 通道，需在此补 provider key，统计归因才不退化为 unknown
        tools = [start, check, cancel, followup_tool, listing]
        for tool in tools:
            metadata = getattr(tool, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = {}
                tool.metadata = metadata
            metadata.setdefault("noesis_provider_key", "builtin")
        logger.info("subagent tools middleware ready session_id={}", session_id)
        return tools


def _command_with_identity(
    tool_call_id: str,
    text: str,
    task: dict[str, Any],
) -> Command:
    """以 Command 返回工具文本，同时把任务身份写入 ``bg_tasks`` state。"""
    task_id = str(task["task_id"])
    public_id = str(task.get("child_session_id") or task_id)
    identity = BgTaskIdentity(
        task_id=task_id,
        child_session_id=public_id,
        subagent_type=str(task.get("subagent_type") or "general"),
        description=str(task.get("description") or ""),
        last_status=str(task.get("status") or ""),
    )
    return Command(
        update={
            "messages": [ToolMessage(text, tool_call_id=tool_call_id)],
            "bg_tasks": {task_id: identity},
        }
    )


__all__ = [
    "BgTaskIdentity",
    "NoesisSubagentMiddleware",
    "PRIVATE_STATE_KEYS",
    "SubagentTasksState",
    "_StartTaskArgs",
]
