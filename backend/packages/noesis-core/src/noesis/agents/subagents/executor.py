"""进程内后台子 Agent 执行器。

全异步 task：``start_task`` 立即返回 task_id，子 Agent 在专用守护线程的
独立事件循环里运行，生命周期归属 session 而非主 run——主 run 结束后任务
继续跑，任意后续轮次 ``check_task`` 收结果。

执行模型参考 deer-flow SubagentExecutor（隔离 loop + 进程级注册表 +
状态机 + 并发上限），但不做工具内轮询：start/check 拆开暴露给模型，
语义对齐 deepagents AsyncSubAgentMiddleware 的工具面，执行层为本进程。

HITL 工具审批：子 Agent 带 checkpointer + interrupt_on 编译，遇审批工具
时 LangGraph 落 checkpoint 并 interrupt；executor 捕获 ``__interrupt__``
转 ``awaiting_approval``，审批经 ``Command(resume={"decisions": [...]})``
在同一 thread 续跑（与主 run HITL 的 resume 契约一致）。

执行面（协程、future、followup 队列）完全在进程内：进程重启即丢，与
dsh ``ctx.jobs`` / deer-flow 注册表同构。subagent 任务的产品数据由标准
``TChatSession/TChatMessage/TAgentRun`` 持久化（见 SubagentSessionService）；
shell job 是易逝的运行时作业，不做持久化。
"""

from __future__ import annotations

import asyncio
import collections
import inspect
import re
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from noesis.chat.runs import RunStatus

from noesis.agents.subagents import notifications
from noesis.runtime.logging import logger
from noesis.services.subagent_runtime_port import configure_executor_port

# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


class BgTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            BgTaskStatus.COMPLETED,
            BgTaskStatus.FAILED,
            BgTaskStatus.CANCELLED,
            BgTaskStatus.TIMED_OUT,
        }


# 占用会话并发槽的状态：排队（QUEUED）只占队列不占槽
_SLOT_STATUSES = frozenset({BgTaskStatus.RUNNING, BgTaskStatus.AWAITING_APPROVAL})


@dataclass
class BackgroundTask:
    """一个后台任务的公开快照（可安全序列化给 API / 工具）。"""

    # 子 Agent 会话 ID；执行状态与会话历史使用同一身份。
    task_id: str
    session_id: str
    user_id: str
    description: str
    # 完整任务指令（子 Agent 首轮输入）；缺省回退 description（旧调用兼容）
    prompt: Optional[str] = None
    child_session_id: Optional[str] = None
    created_by_tool_call_id: Optional[str] = None
    # 标准 child session 对应的 AgentRun；shell job 无此字段。
    run_id: Optional[str] = None
    assistant_message_id: Optional[str] = None
    turn_count: int = 1
    projection_sequence: int = field(default=0, repr=False)
    message_offset: int = field(default=0, repr=False)
    # subagent 任务均可经 send_message 追加 turn；shell 任务使用独立 kind。
    kind: str = "subagent"
    # worker 的 model_id：上下文窗口上限解析用（主对话同源 model_limits）
    model_id: Optional[str] = None
    # 最近一次上下文快照（worker usage 提取；变更才发布/落库）
    context_snapshot: Optional[dict[str, Any]] = None
    status: BgTaskStatus = BgTaskStatus.RUNNING
    result: Optional[str] = None
    error: Optional[str] = None
    interrupt: Optional[dict[str, Any]] = None
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    # 执行过程摘要（有界，前端任务卡展开显示）；lock 保护跨线程读写
    progress: "collections.deque[dict[str, Any]]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_PROGRESS_ENTRIES),
    )
    progress_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self, *, include_progress: bool = True) -> dict[str, Any]:
        data = {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "child_session_id": self.child_session_id,
            "created_by_tool_call_id": self.created_by_tool_call_id,
            "user_id": self.user_id,
            "description": self.description,
            "run_id": self.run_id,
            "assistant_message_id": self.assistant_message_id,
            "turn_count": self.turn_count,
            "kind": self.kind,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "interrupt": self.interrupt,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            # UI 只显示步数；SSE/列表负载裁掉明细，详情走 messages API
            "progress_count": len(self.progress),
        }
        if include_progress:
            data["progress"] = list(self.progress)
        return data


# ---------------------------------------------------------------------------
# 隔离事件循环：后台任务不是主 run 任务树的子节点，主 run 结束不回收
# ---------------------------------------------------------------------------

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_loop_ready: Optional[threading.Event] = None
_loop_lock = threading.Lock()


def _run_isolated_loop(ready: threading.Event) -> None:
    global _loop
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    ready.set()
    try:
        loop.run_forever()
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop_thread, _loop_ready
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        ready = threading.Event()
        _loop_thread = threading.Thread(
            target=_run_isolated_loop,
            args=(ready,),
            daemon=True,
            name="noesis-bg-subagent-loop",
        )
        _loop_thread.start()
        ready.wait(timeout=5)
        assert _loop is not None
        return _loop


def shutdown_loop() -> None:
    """进程退出时停掉隔离 loop（FastAPI lifespan 调用）。"""
    global _loop, _loop_thread
    with _loop_lock:
        loop = _loop
        _loop = None
        _loop_thread = None
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)


# ---------------------------------------------------------------------------
# 注册表与执行器
# ---------------------------------------------------------------------------


@dataclass
class _TaskEntry:
    task: BackgroundTask
    # 在隔离 loop 内惰性调用的 worker 编译工厂（async）：
    # worker 的 LLM 客户端 / checkpointer 连接池必须绑定隔离 loop，
    # 不得复用主 loop 创建的实例（cross-loop 风险）。
    # shell 任务不经 worker 编译（None），直接经 shell_backend 执行
    agent_factory: Optional[Callable[..., Any]]
    recursion_limit: int
    # > 0 时 watchdog 超时取消执行 future；0 = 不限时（shell 任务默认）
    timeout_seconds: float
    hitl_timeout_seconds: float
    followup_factory: Optional[Callable[[str, str, Optional[str]], Any]] = None
    # 排队唤醒时按该值判断槽位（executor 实例不共享，cap 记在条目上）
    session_max_concurrent: int = 1
    # followup-turn 队列：send_message 入队，当前 turn 结束后链式开新 turn
    followups: "collections.deque[str]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_FOLLOWUPS),
    )
    followup_message_ids: "collections.deque[Optional[str]]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_FOLLOWUPS),
    )
    # 与 followups 逐条对应的模型覆盖（None = 沿用当前模型）
    followup_models: "collections.deque[Optional[str]]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_FOLLOWUPS),
    )
    # 生效中的模型覆盖：非 None 时 _ensure_agent 以该模型重新编译 worker
    model_override: Optional[str] = None
    followup_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # factory 首次调用后在隔离 loop 内缓存编译结果（同 executor 任务复用）
    compiled_agent: Any = None
    compiled_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 当前执行协程的 future（用于超时/取消）
    future: Optional[Future] = None
    watchdog_handle: Optional[asyncio.TimerHandle] = None
    # kind="shell"：命令与执行 backend（local_shell 宿主机 / docker 容器）
    shell_command: Optional[str] = None
    shell_backend: Any = None
    # 命令级超时（None=不向 backend 传 timeout，走 backend 默认）
    shell_command_timeout: Optional[int] = None


_TASKS: dict[str, _TaskEntry] = {}
_TASKS_LOCK = threading.Lock()
# 会话级排队任务（超出并发上限时 FIFO 等待，不占并发槽、不启动 watchdog）
_PENDING_QUEUES: dict[str, list[_TaskEntry]] = {}


# ---------------------------------------------------------------------------
# 会话级事件订阅（SSE push，替代前端轮询）：executor 在隔离线程发布，
# 经 call_soon_threadsafe 跨 loop 投递到订阅者的 asyncio.Queue
# ---------------------------------------------------------------------------

_BGSub = tuple[asyncio.AbstractEventLoop, asyncio.Queue, str]  # (loop, queue, user_id)
_SUBSCRIBERS: dict[str, list[_BGSub]] = {}
_SUBSCRIBERS_LOCK = threading.Lock()
_RUN_SUBSCRIBERS: dict[str, list[_BGSub]] = {}
_RUN_SUBSCRIBERS_LOCK = threading.Lock()
_RUN_EVENT_HISTORY: dict[str, collections.deque[dict[str, Any]]] = {}
_RUN_EVENT_HISTORY_LIMIT = 128


def subscribe_bg_events(session_id: str, user_id: str) -> asyncio.Queue:
    """在调用方事件循环上注册订阅（SSE 端点连接时调用）。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.setdefault(session_id, []).append((loop, queue, user_id))
    return queue


def unsubscribe_bg_events(session_id: str, queue: asyncio.Queue) -> None:
    with _SUBSCRIBERS_LOCK:
        subs = _SUBSCRIBERS.get(session_id) or []
        _SUBSCRIBERS[session_id] = [s for s in subs if s[1] is not queue]


def publish_session_event(
    session_id: str, user_id: str, payload: dict[str, Any]
) -> None:
    """向该会话订阅者推送会话级事件（如 continuation run 启动）。"""
    with _SUBSCRIBERS_LOCK:
        subs = list(_SUBSCRIBERS.get(session_id) or [])
    for loop, queue, sub_user in subs:
        if user_id not in (None, sub_user):
            continue

        def _put(q: asyncio.Queue = queue, p: dict[str, Any] = payload) -> None:
            try:
                q.put_nowait(p)
            except asyncio.QueueFull:
                pass

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass


def subscribe_run_events(run_id: str, user_id: str) -> asyncio.Queue:
    """按标准 AgentRun 订阅 child session 事件（详情打开时使用）。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _RUN_SUBSCRIBERS_LOCK:
        _RUN_SUBSCRIBERS.setdefault(run_id, []).append((loop, queue, user_id))
    return queue


def unsubscribe_run_events(run_id: str, queue: asyncio.Queue) -> None:
    with _RUN_SUBSCRIBERS_LOCK:
        subs = _RUN_SUBSCRIBERS.get(run_id) or []
        remaining = [s for s in subs if s[1] is not queue]
        if remaining:
            _RUN_SUBSCRIBERS[run_id] = remaining
        else:
            _RUN_SUBSCRIBERS.pop(run_id, None)


def get_run_event_history(run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
    """返回最近一段可重放事件；详情断线重连不依赖进程内 queue 的存活。"""
    with _RUN_SUBSCRIBERS_LOCK:
        history = list(_RUN_EVENT_HISTORY.get(run_id) or ())
    return [
        item for item in history
        if int(item.get("sequence") or 0) > max(0, after_sequence)
        or (after_sequence <= 0 and item.get("type") == "run.started")
    ]


def _publish_run_event(
    task: BackgroundTask,
    event: str,
    *,
    content: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
) -> None:
    if not task.run_id:
        return
    payload = {
        "type": event,
        "run_id": task.run_id,
        "session_id": task.child_session_id or task.task_id,
        "sequence": task.projection_sequence,
        "status": task.status.value,
    }
    # 终态时间：前端据此冻结 duration（重放历史事件同样可得）
    if task.completed_at is not None:
        payload["finished_at"] = task.completed_at
    if content is not None:
        payload["content"] = content
        if isinstance(content, dict) and isinstance(content.get("_pending_hitl"), dict):
            payload["pending_hitl"] = content["_pending_hitl"]
    if context is not None:
        payload["context"] = context
    with _RUN_SUBSCRIBERS_LOCK:
        history = _RUN_EVENT_HISTORY.setdefault(
            task.run_id,
            collections.deque(maxlen=_RUN_EVENT_HISTORY_LIMIT),
        )
        history.append(dict(payload))
        subs = list(_RUN_SUBSCRIBERS.get(task.run_id) or [])
    for loop, queue, user_id in subs:
        if task.user_id not in (None, user_id):
            continue

        def _put(q: asyncio.Queue = queue, p: dict[str, Any] = payload) -> None:
            try:
                q.put_nowait(p)
            except asyncio.QueueFull:
                pass

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass
    if event == "run.finished":
        def _expire_history(run_id: str = task.run_id) -> None:
            with _RUN_SUBSCRIBERS_LOCK:
                _RUN_EVENT_HISTORY.pop(run_id, None)

        timer = threading.Timer(300.0, _expire_history)
        timer.daemon = True
        timer.start()
    # 父会话只接收摘要目录更新；正文仍只在 child drawer 打开时订阅 run SSE。
    if task.child_session_id:
        publish_session_event(
            task.session_id,
            task.user_id,
            {
                "event": "child-session",
                "child": {
                    "session_id": task.child_session_id,
                    "parent_id": task.session_id,
                    "created_by_tool_call_id": task.created_by_tool_call_id,
                    "title": task.description,
                    "profile_id": "task-worker",
                    "run_id": task.run_id,
                    "status": task.status.value,
                    "step_count": len(task.progress),
                    "started_at": task.started_at,
                    "finished_at": task.completed_at,
                    "interrupt": task.interrupt,
                },
            },
        )


def _publish_task_event(task: BackgroundTask, event: str) -> None:
    """向该会话所有订阅者推送任务快照事件；慢消费者丢事件（重连快照兜底）。"""
    payload = {"event": event, "task": task.to_dict(include_progress=False)}
    with _SUBSCRIBERS_LOCK:
        subs = list(_SUBSCRIBERS.get(task.session_id) or [])
    for loop, queue, user_id in subs:
        if task.user_id not in (None, user_id):
            continue

        def _put(q: asyncio.Queue = queue, p: dict = payload) -> None:
            try:
                q.put_nowait(p)
            except asyncio.QueueFull:
                pass

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # 订阅者 loop 已关闭（SSE 断开竞态）


# 默认值；装配方（super_agent）可用 config 覆盖
MAX_CONCURRENT_PER_SESSION = 3
TASK_TIMEOUT_SECONDS = 900.0
# 后台命令任务超时：默认 0=不限时（长命令正是后台化动机，防泄漏靠
# cancel_task + 会话容器生命周期兜底）
SHELL_TASK_TIMEOUT_SECONDS = 0.0
HITL_TIMEOUT_SECONDS = 86400.0
# followup 消息上限（超出丢最旧）
MAX_FOLLOWUPS = 10
# 执行过程摘要上限（超出丢最旧）
MAX_PROGRESS_ENTRIES = 50
_PROGRESS_PREVIEW_CHARS = 120
# shell 任务结果中 stdout/stderr 尾部保留长度
_SHELL_RESULT_TAIL_CHARS = 4000
# 后台命令默认命令级超时（模型未显式传 timeout 时）：对齐 deepagents
# execute 工具的 max_execute_timeout；docker runner 侧 0=不限时由模型显式传
_SHELL_DEFAULT_COMMAND_TIMEOUT = 3600


def _extract_interrupt_payload(interrupts: Any) -> Optional[dict[str, Any]]:
    """LangGraph ``__interrupt__`` → {interrupt_id, action_requests, kind}。"""
    first = interrupts[0] if isinstance(interrupts, (list, tuple)) else interrupts
    if first is None:
        return None
    iid = getattr(first, "id", None) or (
        first.get("id") if isinstance(first, dict) else None
    )
    value = getattr(first, "value", None)
    if value is None and isinstance(first, dict):
        value = first.get("value")
    payload = dict(value) if isinstance(value, dict) else {"action_requests": []}
    if not iid:
        return None
    return {"interrupt_id": str(iid), **payload}


def _progress_append(task: BackgroundTask, entry: dict[str, Any]) -> None:
    with task.progress_lock:
        task.progress.append(entry)


def _maybe_update_context_snapshot(task: BackgroundTask, messages: list) -> None:
    """从 worker 最新一轮模型调用的 usage 提取上下文快照。

    主对话的快照由 SSE bridge 在模型调用边界提取（usage.input_tokens，
    单轮真实值、每次覆盖）；子 run 无 bridge，这里从 thread 消息的
    usage_metadata 取同一口径。变更时发布 context-update run 事件并
    落库到子会话 extra.context（与主对话同存储位）。
    """
    if not task.child_session_id:
        return
    snapshot = None
    for message in reversed(messages):
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, dict) and usage.get("input_tokens"):
            from noesis.llm.model_limits import resolve_context_max_tokens
            from noesis.chat.event_mapping.usage_normalize import compute_used_percentage

            current = int(usage["input_tokens"])
            limit = resolve_context_max_tokens(task.model_id)
            snapshot = {
                "current_tokens": current,
                "max_tokens": limit,
                "used_percentage": compute_used_percentage(current, limit),
            }
            break
    if snapshot is None or snapshot == getattr(task, "context_snapshot", None):
        return
    task.context_snapshot = snapshot
    _publish_run_event(task, "context-update", context=snapshot)
    _schedule_context_persist(task, snapshot)


def _schedule_context_persist(task: BackgroundTask, snapshot: dict[str, Any]) -> None:
    """快照落库到子会话 extra.context（DB 引擎绑定主 loop，跨 loop 调度）。"""
    import datetime as _dt

    from noesis.runtime.main_loop import run_on_main_loop

    payload = {**snapshot, "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}

    async def _merge() -> None:
        try:
            from noesis.services.chat_service import ChatService
            from noesis.storage.postgres.manager import pg_manager

            async with pg_manager.get_async_session_context() as db:
                await ChatService.merge_session_extra(
                    task.child_session_id, task.user_id, {"context": payload}, db=db,
                )
        except Exception:
            logger.opt(exception=True).warning(
                "bg subagent context snapshot persist failed task_id={}",
                task.task_id,
            )

    run_on_main_loop(_merge(), name=f"bg-ctx:{task.task_id}")


def _record_step_progress(
    task: BackgroundTask, final_state: Any, seen_ids: set
) -> bool:
    """从 values 快照 diff 出新增消息，记录轻量步骤摘要。

    deer-flow capture_new_step_messages 的简化版：按消息 id 去重，
    AIMessage 记文本片段/工具调用名，ToolMessage 记名称与状态。
    """
    changed = False
    messages = final_state.get("messages", []) if isinstance(final_state, dict) else []
    for message in messages:
        mid = getattr(message, "id", None) or id(message)
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        name = getattr(message, "name", None) or ""
        if isinstance(message, AIMessage):
            calls = getattr(message, "tool_calls", None) or []
            for call in calls:
                changed = True
                _progress_append(
                    task,
                    {
                        "kind": "tool_call",
                        "name": str(call.get("name") or ""),
                        "ts": time.time(),
                    },
                )
            text = _final_answer_text({"messages": [message]})
            if text:
                changed = True
                _progress_append(
                    task,
                    {
                        "kind": "text",
                        "preview": text[:_PROGRESS_PREVIEW_CHARS],
                        "ts": time.time(),
                    },
                )
        elif isinstance(message, ToolMessage):
            changed = True
            content = (
                message.content
                if isinstance(message.content, str)
                else str(message.content)
            )
            _progress_append(
                task,
                {
                    "kind": "tool_result",
                    "name": str(name),
                    "status": str(getattr(message, "status", None) or "success"),
                    "preview": content[:_PROGRESS_PREVIEW_CHARS],
                    "ts": time.time(),
                },
            )
    return changed


def _child_message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _fallback_terminal(fallback_error: Optional[str]) -> tuple[RunStatus, str]:
    """最后模型调用为降级失败说明时的 run 终态与 finish_reason。"""
    if fallback_error:
        return RunStatus.ERROR, "error"
    return RunStatus.COMPLETED, "stop"


def _final_model_fallback_error(messages: list[Any]) -> Optional[str]:
    """最后一次模型调用若为 LLM 降级失败说明，返回其文本。

    middleware 重试耗尽会返回 content 为失败文案的 AIMessage（带
    noesis_model_fallback 标记）；此时 run 不能标 completed——否则父 Agent
    会把「服务暂时不可用」当作子任务产出。
    """
    from noesis.agents.middlewares.llm_error_handling_middleware import is_model_fallback_message

    for message in reversed(messages):
        if isinstance(message, AIMessage):
            if is_model_fallback_message(message):
                return _child_message_text(message) or "模型服务暂不可用"
            return None
    return None


def _child_projection_content(messages: list[Any]) -> dict[str, Any]:
    """将 LangGraph 当前消息快照折叠成主 Agent 使用的 multipart。"""
    parts: list[dict[str, Any]] = []
    tool_parts: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, HumanMessage):
            continue
        if isinstance(message, AIMessage):
            text = _child_message_text(message)
            if text.strip():
                parts.append({"type": "text", "id": str(getattr(message, "id", None) or uuid.uuid4()), "content": text})
            for call in getattr(message, "tool_calls", None) or []:
                call_id = str(call.get("id") or uuid.uuid4())
                tool_part = {
                    "type": "tool",
                    "id": call_id,
                    "tool_call_id": call_id,
                    "name": str(call.get("name") or "tool"),
                    "input": call.get("args") or {},
                    "output": "",
                    "status": "running",
                    "state": "running",
                }
                parts.append(tool_part)
                tool_parts[call_id] = tool_part
        elif isinstance(message, ToolMessage):
            call_id = str(getattr(message, "tool_call_id", None) or "")
            target = tool_parts.get(call_id)
            if target is None:
                target = {
                    "type": "tool",
                    "id": call_id or str(getattr(message, "id", None) or uuid.uuid4()),
                    "tool_call_id": call_id,
                    "name": str(getattr(message, "name", None) or "tool"),
                    "input": {},
                    "output": "",
                }
                parts.append(target)
                if call_id:
                    tool_parts[call_id] = target
            target["output"] = _child_message_text(message)
            target["status"] = str(getattr(message, "status", None) or "success")
            target["state"] = "failed" if target["status"] == "error" else "succeeded"
            if target.get("name") == "start_task":
                child_ref = re.search(
                    r"(?:子 Agent 已启动|任务完成|任务等待用户审批)(?:[：:]\s*|（)([0-9a-f-]{36})",
                    target["output"],
                    flags=re.IGNORECASE,
                )
                if child_ref:
                    target["child_session_id"] = child_ref.group(1)
    return {"version": 1, "parts": parts}


async def _persist_child_projection(task: BackgroundTask, messages: list[Any], *, status: str = "streaming") -> None:
    if not task.run_id or not task.assistant_message_id:
        return
    task.projection_sequence += 1
    from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
    from noesis.runtime.main_loop import run_on_main_loop

    future = run_on_main_loop(
        SubagentSessionService.persist_projection(
            run_id=task.run_id,
            assistant_message_id=task.assistant_message_id,
            content=_child_projection_content(messages),
            sequence=task.projection_sequence,
            status=status,
        ),
        name=f"subagent-projection:{task.run_id}:{task.projection_sequence}",
    )
    if future is not None:
        await asyncio.wrap_future(future)
    _publish_run_event(
        task,
        "message.updated",
        content=_child_projection_content(messages),
    )


def _notify_terminal(task: BackgroundTask) -> None:
    """终态转换点统一记录会话通知（completed/failed/timed_out/cancelled）。"""
    notifications.record(
        session_id=task.session_id,
        task_id=task.child_session_id or task.task_id,
        status=task.status.value,
        preview=task.result or task.error,
        label=task.description,
        step_count=len(task.progress),
        turn_count=task.turn_count if task.kind == "subagent" else None,
        duration_ms=(
            int(max(0.0, (task.completed_at or time.time()) - task.started_at) * 1000)
            if task.started_at else None
        ),
    )
    _schedule_continuation(task)
    _drain_session_queue(task.session_id)


def _schedule_continuation(task: BackgroundTask) -> None:
    """终态后尝试唤醒主 Agent（dsh parent.followup 的 run 级等价物）。

    无活跃 run 时自动创建 continuation run；调度回主 loop（DB 引擎与
    RunManager 绑定主 loop）。仅 completed 触发——失败/取消的交付由模型
    在下次交互时按通知自行决定，自动唤醒只会空转。
    经 schedule_maybe_continue 去抖：窗口内多个终态合并为一次唤醒，
    避免每个任务终态各产生一个重复发送全量上下文的 run。
    """
    if task.status != BgTaskStatus.COMPLETED:
        return
    from noesis.runtime.main_loop import run_on_main_loop
    from noesis.services.bg_continuation_service import schedule_maybe_continue

    run_on_main_loop(
        schedule_maybe_continue(task.session_id, task.user_id),
        name=f"bg-continue:{task.task_id}",
    )


def _final_answer_text(final_state: Any) -> str:
    messages = final_state.get("messages", []) if isinstance(final_state, dict) else []
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            if text.strip():
                return text
    return ""


class BackgroundSubagentExecutor:
    """start/check/cancel/list 的进程内执行面。"""

    def __init__(
        self,
        *,
        max_concurrent_per_session: int = MAX_CONCURRENT_PER_SESSION,
        task_timeout_seconds: float = TASK_TIMEOUT_SECONDS,
        shell_task_timeout_seconds: float = SHELL_TASK_TIMEOUT_SECONDS,
        hitl_timeout_seconds: float = HITL_TIMEOUT_SECONDS,
        recursion_limit: int = 9999,
    ) -> None:
        self._max_concurrent = max(1, max_concurrent_per_session)
        self._task_timeout = task_timeout_seconds
        self._shell_timeout = max(0.0, shell_task_timeout_seconds)
        self._hitl_timeout = hitl_timeout_seconds
        self._recursion_limit = recursion_limit

    # -- 查询（任意线程安全调用） ------------------------------------

    @staticmethod
    def get(task_id: str) -> Optional[dict[str, Any]]:
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
            if entry is None:
                entry = next(
                    (item for item in _TASKS.values() if item.task.child_session_id == task_id),
                    None,
                )
            if entry is not None:
                return entry.task.to_dict(include_progress=False)
        return None

    @staticmethod
    def get_memory(task_id: str) -> Optional[dict[str, Any]]:
        """只查进程内注册表，供 async catalog 避免同步数据库 fallback。"""
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id) or next(
                (item for item in _TASKS.values() if item.task.child_session_id == task_id),
                None,
            )
            return entry.task.to_dict(include_progress=False) if entry else None

    @staticmethod
    def list_for_session(session_id: str) -> list[dict[str, Any]]:
        with _TASKS_LOCK:
            tasks = {
                entry.task.task_id: entry.task.to_dict(include_progress=False)
                for entry in _TASKS.values()
                if entry.task.session_id == session_id
            }
        return sorted(tasks.values(), key=lambda t: t["started_at"])

    @staticmethod
    def pending_approvals(session_id: str) -> list[dict[str, Any]]:
        return [
            t
            for t in BackgroundSubagentExecutor.list_for_session(session_id)
            if t["status"] == BgTaskStatus.AWAITING_APPROVAL.value
        ]

    # -- 启动 ---------------------------------------------------------

    def start(
        self,
        *,
        worker_factory: Callable[..., Any],
        description: str,
        prompt: Optional[str] = None,
        session_id: str,
        user_id: str,
        child_session_id: Optional[str] = None,
        created_by_tool_call_id: Optional[str] = None,
        task_id: Optional[str] = None,
        run_id: Optional[str] = None,
        assistant_message_id: Optional[str] = None,
        followup_factory: Optional[Callable[[str, str, Optional[str]], Any]] = None,
        model_id: Optional[str] = None,
    ) -> str:
        """启动后台任务，立即返回 task_id；超并发上限时按会话 FIFO 排队。

        description = 简短标题（任务卡/列表展示）；prompt = 完整任务指令
        （子 Agent 首轮输入，缺省回退 description）。
        """
        task_id = task_id or f"bg-{uuid.uuid4()}"
        task = BackgroundTask(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            description=description,
            prompt=prompt,
            child_session_id=child_session_id,
            created_by_tool_call_id=created_by_tool_call_id,
            run_id=run_id,
            assistant_message_id=assistant_message_id,
            kind="subagent",
            model_id=model_id,
        )
        entry = _TaskEntry(
            task=task,
            agent_factory=worker_factory,
            followup_factory=followup_factory,
            recursion_limit=self._recursion_limit,
            timeout_seconds=self._task_timeout,
            hitl_timeout_seconds=self._hitl_timeout,
        )
        self._launch(entry)
        return task_id

    def start_shell(
        self,
        *,
        command: str,
        backend: Any,
        session_id: str,
        user_id: str,
        timeout: Optional[int] = None,
        description: Optional[str] = None,
    ) -> str:
        """启动后台命令任务（kind="shell"）：不经 worker 编译，直接经
        backend 执行；任务超时独立（shell_task_timeout_seconds，默认 0=不限
        时），并发上限与状态机复用。无 awaiting_approval（审批在工具调用时
        已发生）。

        ``timeout`` 为命令级超时（透传 backend）：None 用默认（1h）；
        docker runner 侧 0=不限时（local_shell 不接受 0，同前台语义）。
        ``description`` 为任务卡展示用简短说明；缺省回退原始命令。
        """
        task_id = f"bg-{uuid.uuid4()}"
        task = BackgroundTask(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            description=description or command,
            kind="shell",
        )
        entry = _TaskEntry(
            task=task,
            agent_factory=None,
            recursion_limit=self._recursion_limit,
            timeout_seconds=self._shell_timeout,
            hitl_timeout_seconds=self._hitl_timeout,
            shell_command=command,
            shell_backend=backend,
            shell_command_timeout=(
                timeout if timeout is not None else _SHELL_DEFAULT_COMMAND_TIMEOUT
            ),
        )
        self._launch(entry)
        return task_id

    def _launch(self, entry: _TaskEntry) -> None:
        """并发预检 + 插入注册表 + 调度执行（subagent / shell 同一入口）。

        超上限不再拒绝：任务置 QUEUED 按会话 FIFO 排队，任一同会话任务落
        终态后由 _drain_session_queue 调度。排队等待不占并发槽、不启动
        watchdog（900s 预算从实际开始执行起算）。上限检查与插入同锁，
        避免并发 start 的 TOCTOU 竞态。
        """
        task = entry.task
        session_id = task.session_id
        entry.session_max_concurrent = self._max_concurrent
        with _TASKS_LOCK:
            active = sum(
                1
                for e in _TASKS.values()
                if e.task.session_id == session_id
                and e.task.status in _SLOT_STATUSES
            )
            _TASKS[task.task_id] = entry
            if active >= self._max_concurrent:
                task.status = BgTaskStatus.QUEUED
                _PENDING_QUEUES.setdefault(session_id, []).append(entry)
                pending = len(_PENDING_QUEUES[session_id])
                queued = True
            else:
                queued = False
                _schedule_entry_locked(entry)
        if queued:
            # started 事件驱动目录刷新（消费端按 task_id upsert 幂等）；
            # drain 唤醒时会再发一次，目录二次刷新无副作用
            _publish_task_event(task, "started")
            logger.info(
                "bg task queued task_id={} session_id={} kind={} pending={}",
                task.task_id, session_id, task.kind, pending,
            )
            return
        _publish_entry_started(entry)
        logger.info(
            "bg task started task_id={} session_id={} kind={} active={}/{}",
            task.task_id,
            session_id,
            task.kind,
            active + 1,
            self._max_concurrent,
        )

    @staticmethod
    def validate_followup(task_id: str) -> None:
        """在写入标准 user message 前校验任务仍可接受追问。"""
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id) or next(
                (item for item in _TASKS.values() if item.task.child_session_id == task_id),
                None,
            )
            if entry is None:
                raise ValueError(f"后台任务不存在: {task_id}")
            task = entry.task
            if task.kind == "shell":
                raise ValueError("该任务为后台命令任务，不支持追加消息")
            if task.status.is_terminal and task.status != BgTaskStatus.COMPLETED:
                raise ValueError(f"任务已结束（{task.status.value}），无法追加消息")

    @staticmethod
    def send_message(
        task_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """followup-turn：向子任务追加一个 turn。

        - running / awaiting_approval：入队，当前 turn 结束后链式开新 turn
        - completed：冷恢复——同 thread 开新 turn，任务回到 running
        - shell / failed / timed_out / cancelled：拒绝
        - model_id 非空且与当前不同：该 turn 起以新模型编译 worker（同 thread 续跑）
        """
        text = message.strip()
        if not text:
            raise ValueError("消息不能为空")
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
            if entry is None:
                entry = next(
                    (item for item in _TASKS.values() if item.task.child_session_id == task_id),
                    None,
                )
            if entry is None:
                raise ValueError(f"后台任务不存在: {task_id}")
            task = entry.task
            if task.kind == "shell":
                raise ValueError("该任务为后台命令任务，不支持追加消息（可用 check_task 收取输出、重新执行请新建命令）")
            status = task.status
            # completed → 冷恢复：同 thread 开新 turn（followup 队列一并排入）
            if status == BgTaskStatus.COMPLETED:
                task.status = BgTaskStatus.RUNNING
                task.result = None
                task.completed_at = None
                loop = _ensure_loop()
                entry.future = asyncio.run_coroutine_threadsafe(
                    _arun_followup(entry, text, user_message_id, model_id),
                    loop,
                )
                _arm_watchdog(entry)
                _publish_task_event(task, "followup")
                return task.to_dict()
            if status.is_terminal:
                raise ValueError(f"任务已结束（{status.value}），无法追加消息")
            with entry.followup_lock:
                entry.followups.append(text)
                entry.followup_message_ids.append(user_message_id)
                entry.followup_models.append(model_id)
            _publish_task_event(task, "followup")
            return task.to_dict()

    @staticmethod
    def pop_followups(entry: _TaskEntry) -> list[str]:
        """取出待续 turn 消息（链式调度点消费）。"""
        with entry.followup_lock:
            messages = list(entry.followups)
            entry.followups.clear()
            entry.followup_message_ids.clear()
            entry.followup_models.clear()
            return messages

    @staticmethod
    def get_future(task_id: str) -> Optional[Future]:
        """取当前执行 future（前台等待用）。"""
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
            if entry is None:
                entry = next(
                    (item for item in _TASKS.values() if item.task.child_session_id == task_id),
                    None,
                )
            return entry.future if entry else None

    # -- 审批 / 取消 ---------------------------------------------------

    @staticmethod
    def submit_decisions(
        task_id: str, decisions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """审批决策（approve / reject）→ 在同一 thread 续跑子 Agent。"""
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
            if entry is None:
                entry = next(
                    (item for item in _TASKS.values() if item.task.child_session_id == task_id),
                    None,
                )
            if entry is None:
                raise ValueError(f"后台任务不存在: {task_id}")
            if entry.task.status != BgTaskStatus.AWAITING_APPROVAL:
                raise ValueError(
                    f"任务不在待审批状态（当前 {entry.task.status.value}）"
                )
            entry.task.status = BgTaskStatus.RUNNING
            entry.task.interrupt = None
            entry.task.projection_sequence += 1
        _publish_task_event(entry.task, "followup")
        _publish_run_event(entry.task, "approval.resumed")
        if entry.task.run_id:
            from noesis.runtime.main_loop import run_on_main_loop
            from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService

            run_on_main_loop(
                SubagentSessionService.mark_resumed(entry.task.run_id),
                name=f"subagent-resume:{entry.task.run_id}",
            )
        loop = _ensure_loop()
        entry.future = asyncio.run_coroutine_threadsafe(
            _arun(entry, resume_command=Command(resume={"decisions": decisions})),
            loop,
        )
        _arm_watchdog(entry)
        return entry.task.to_dict(include_progress=False)

    @staticmethod
    def cancel(task_id: str) -> dict[str, Any]:
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
            if entry is None:
                entry = next(
                    (item for item in _TASKS.values() if item.task.child_session_id == task_id),
                    None,
                )
            if entry is None:
                raise ValueError(f"后台任务不存在: {task_id}")
            if entry.task.status.is_terminal:
                return entry.task.to_dict(include_progress=False)
            _disarm_watchdog(entry)
            with entry.followup_lock:
                entry.followups.clear()
                entry.followup_message_ids.clear()
                entry.followup_models.clear()
            if entry.task.status == BgTaskStatus.QUEUED:
                # 排队任务无执行 future，出队即完成取消
                _dequeue_locked(entry.task)
            if entry.future is not None:
                entry.future.cancel()
            entry.task.status = BgTaskStatus.CANCELLED
            entry.task.completed_at = time.time()
            snapshot = entry.task.to_dict(include_progress=False)
        # 锁外发布：_notify_terminal 内的排队唤醒（drain）需要再拿 _TASKS_LOCK
        _publish_task_event(entry.task, "terminal")
        _notify_terminal(entry.task)
        if entry.task.run_id:
            from noesis.runtime.main_loop import run_on_main_loop
            from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService

            run_on_main_loop(
                SubagentSessionService.mark_terminal(
                    run_id=entry.task.run_id,
                    status=RunStatus.PARTIAL,
                    content=None,
                    error="任务已取消",
                    finish_reason="cancelled",
                ),
                name=f"subagent-cancel:{entry.task.run_id}",
            )
        return snapshot

    # -- 内部委托模块实现（见下方模块函数） ----------------------------


def _config(entry: _TaskEntry) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": entry.task.child_session_id or entry.task.task_id},
        "recursion_limit": entry.recursion_limit,
    }


async def _ensure_agent(entry: _TaskEntry) -> Any:
    """惰性编译 worker：factory 在隔离 loop 内调用，其 LLM 客户端 /
    checkpointer 连接池绑定隔离 loop（避免复用主 loop 实例的 cross-loop 风险）。

    entry.model_override 非 None 时以覆盖模型编译（followup 切换模型后
    compiled_agent 已被置空，这里按新模型重建；同 thread 续跑，历史保留）。
    """
    if entry.compiled_agent is None:
        with entry.compiled_lock:
            if entry.compiled_agent is None:
                if entry.model_override is None:
                    result = entry.agent_factory()
                else:
                    result = entry.agent_factory(entry.model_override)
                if inspect.isawaitable(result):
                    result = await result
                entry.compiled_agent = result
    return entry.compiled_agent


def _apply_model_override(entry: _TaskEntry, model_id: Optional[str]) -> bool:
    """切换任务模型：更新 task.model_id（上下文窗口口径跟随）并使已编译
    worker 失效，下一 turn 以新模型编译。返回是否发生切换。"""
    if not model_id or model_id == entry.task.model_id:
        return False
    entry.task.model_id = model_id
    entry.model_override = model_id
    with entry.compiled_lock:
        entry.compiled_agent = None
    return True


def _pop_first_followup(entry: _TaskEntry) -> Optional[tuple[str, Optional[str], Optional[str]]]:
    with entry.followup_lock:
        if not entry.followups:
            return None
        text = entry.followups.popleft()
        message_id = entry.followup_message_ids.popleft() if entry.followup_message_ids else None
        model_id = entry.followup_models.popleft() if entry.followup_models else None
        return text, message_id, model_id


async def _arun(
    entry: _TaskEntry,
    *,
    initial_source: Any = None,
    resume_command: Optional[Command] = None,
) -> None:
    """执行一轮或多轮 turn。

    - start：initial_source 为原始 description 的 HumanMessage state
    - 审批 resume：resume_command 为 Command(resume=decisions)
    - 冷恢复（send_message 对 completed 任务）：initial_source 为追加消息
    - kind="shell"：分派到 _arun_shell（无 worker / 无 turn 概念）
    turn 正常结束后若 followup 队列非空，链式开下一个 turn（同 thread
    追加 HumanMessage），队列清空前任务保持 running。
    """
    task = entry.task
    if entry.task.kind == "shell":
        await _arun_shell(entry)
        return
    if task.status.is_terminal:
        # 调度窗口内已被 cancel：终态与通知已由 cancel 发布，直接退出
        return
    try:
        if task.run_id:
            from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
            from noesis.runtime.main_loop import run_on_main_loop

            started_future = run_on_main_loop(
                SubagentSessionService.mark_started(task.run_id),
                name=f"subagent-start:{task.run_id}",
            )
            if started_future is not None:
                await asyncio.wrap_future(started_future)
        agent = await _ensure_agent(entry)
        # 首轮输入：优先显式 resume command（审批续跑），
        # 否则 initial_source（start 的 description / 冷恢复的追加消息）
        source = (
            resume_command
            if resume_command is not None
            else (
                initial_source
                if initial_source is not None
                else {"messages": [HumanMessage(content=task.prompt or task.description)]}
            )
        )
        projection_offset = task.message_offset
        baseline_message_ids: set[Any] = set()
        while True:
            seen_ids: set = set(baseline_message_ids)
            final: Any = None
            # astream(values)：既拿到终态，又能逐步 diff 执行过程摘要
            async for chunk in agent.astream(
                source, _config(entry), stream_mode="values"
            ):
                final = chunk
                if _record_step_progress(task, chunk, seen_ids):
                    messages = chunk.get("messages", []) if isinstance(chunk, dict) else []
                    if task.run_id:
                        await _persist_child_projection(task, messages[projection_offset:])
                    _publish_task_event(task, "progress")
                    _maybe_update_context_snapshot(task, messages)
            interrupts = final.get("__interrupt__") if isinstance(final, dict) else None
            payload = _extract_interrupt_payload(interrupts) if interrupts else None
            if payload is not None:
                task.status = BgTaskStatus.AWAITING_APPROVAL
                task.interrupt = payload
                if task.run_id:
                    from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
                    from noesis.runtime.main_loop import run_on_main_loop

                    hitl_future = run_on_main_loop(
                        SubagentSessionService.mark_waiting_approval(
                            task.run_id,
                            payload,
                            content=_child_projection_content(final.get("messages", [])[projection_offset:]),
                            sequence=task.projection_sequence,
                        ),
                        name=f"subagent-hitl:{task.run_id}",
                    )
                    if hitl_future is not None:
                        await asyncio.wrap_future(hitl_future)
                    approval_content = _child_projection_content(final.get("messages", [])[projection_offset:])
                    approval_content["_pending_hitl"] = payload
                    _publish_run_event(task, "approval.required", content=approval_content)
                _publish_task_event(task, "awaiting_approval")
                _disarm_watchdog(entry)
                _arm_hitl_watchdog(entry)
                logger.info(
                    "bg subagent awaiting approval task_id={} actions={}",
                    task.task_id,
                    len(payload.get("action_requests") or []),
                )
                return
            task.result = _final_answer_text(final)
            # followup 链：队列非空则同 thread 开下一个 turn
            next_followup = _pop_first_followup(entry)
            if next_followup is None:
                break
            next_message, next_user_message_id, next_model_id = next_followup
            # 该 turn 指定了新模型 → 失效已编译 worker，下一轮以新模型续跑同 thread
            if _apply_model_override(entry, next_model_id):
                agent = await _ensure_agent(entry)
                logger.info(
                    "bg subagent model switched task_id={} model={}",
                    task.task_id,
                    next_model_id,
                )
            turn_content = _child_projection_content(
                final.get("messages", [])[projection_offset:] if isinstance(final, dict) else []
            )
            turn_fallback_error = _final_model_fallback_error(
                final.get("messages", []) if isinstance(final, dict) else []
            )
            turn_status, turn_reason = _fallback_terminal(turn_fallback_error)
            projection_offset = len(final.get("messages", [])) if isinstance(final, dict) else 0
            baseline_message_ids = {
                getattr(message, "id", None) or id(message)
                for message in final.get("messages", [])
            } if isinstance(final, dict) else set()
            if task.run_id:
                from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
                from noesis.runtime.main_loop import run_on_main_loop

                current_run_future = run_on_main_loop(
                    SubagentSessionService.mark_terminal(
                        run_id=task.run_id,
                        status=turn_status,
                        content=turn_content,
                        error=turn_fallback_error,
                        finish_reason=turn_reason,
                    ),
                    name=f"subagent-turn-terminal:{task.run_id}",
                )
                if current_run_future is not None:
                    await asyncio.wrap_future(current_run_future)
                if entry.followup_factory is not None:
                    launch = entry.followup_factory(
                        task.child_session_id or task.task_id,
                        next_message,
                        next_user_message_id,
                    )
                    if inspect.isawaitable(launch):
                        launch = await launch
                    task.run_id = str(launch.get("run_id") or "") or None
                    task.assistant_message_id = str(launch.get("assistant_message_id") or "") or None
                    task.turn_count += 1
                    task.projection_sequence = 0
                    task.message_offset = projection_offset
            task.status = BgTaskStatus.RUNNING
            task.completed_at = None
            logger.info(
                "bg subagent followup turn task_id={} queued={}",
                task.task_id,
                len(entry.followups),
            )
            source = {"messages": [HumanMessage(content=next_message)]}
        final_messages = final.get("messages", []) if isinstance(final, dict) else []
        final_fallback_error = _final_model_fallback_error(final_messages)
        final_status, final_reason = _fallback_terminal(final_fallback_error)
        if final_fallback_error:
            task.status = BgTaskStatus.FAILED
            task.error = final_fallback_error
        else:
            task.status = BgTaskStatus.COMPLETED
        task.completed_at = time.time()
        if task.run_id:
            from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
            from noesis.runtime.main_loop import run_on_main_loop

            terminal_future = run_on_main_loop(
                SubagentSessionService.mark_terminal(
                    run_id=task.run_id,
                    status=final_status,
                    content=_child_projection_content(final_messages[projection_offset:]),
                    error=final_fallback_error,
                    finish_reason=final_reason,
                ),
                name=f"subagent-terminal:{task.run_id}",
            )
            if terminal_future is not None:
                await asyncio.wrap_future(terminal_future)
            _publish_run_event(
                task,
                "run.finished",
                content=_child_projection_content(final_messages[projection_offset:]),
            )
        logger.info(
            "bg subagent completed task_id={} steps={} duration={:.1f}s",
            task.task_id,
            len(task.progress),
            task.completed_at - task.started_at,
        )
        _publish_task_event(task, "terminal")
        _notify_terminal(task)
    except asyncio.CancelledError:
        if not task.status.is_terminal:
            task.status = BgTaskStatus.CANCELLED
            task.completed_at = time.time()
    except Exception as exc:
        task.status = BgTaskStatus.FAILED
        task.error = str(exc)
        task.completed_at = time.time()
        if task.run_id:
            from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
            from noesis.runtime.main_loop import run_on_main_loop

            error_future = run_on_main_loop(
                SubagentSessionService.mark_terminal(
                    run_id=task.run_id,
                    status=RunStatus.ERROR,
                    content=None,
                    error=str(exc),
                    finish_reason="error",
                ),
                name=f"subagent-error:{task.run_id}",
            )
            if error_future is not None:
                await asyncio.wrap_future(error_future)
            _publish_run_event(task, "run.finished")
        _publish_task_event(task, "terminal")
        _notify_terminal(task)
        logger.opt(exception=True).error(
            "bg subagent failed task_id={}",
            task.task_id,
        )


async def _arun_followup(
    entry: _TaskEntry,
    text: str,
    user_message_id: Optional[str] = None,
    model_id: Optional[str] = None,
) -> None:
    """completed child session 的新 turn：先建标准 run，再进入 worker。

    model_id 非空且与当前不同：该 turn 起以新模型编译 worker（同 thread 续跑）。
    """
    _apply_model_override(entry, model_id)
    agent = await _ensure_agent(entry)
    entry.task.turn_count += 1
    baseline = 0
    try:
        state = await agent.aget_state(_config(entry))
        values = getattr(state, "values", None)
        baseline = len(values.get("messages", [])) if isinstance(values, dict) else 0
    except Exception:
        logger.warning("读取子 Agent followup 基线失败 task_id={}", entry.task.task_id)
    if entry.followup_factory is not None:
        launch = entry.followup_factory(
            entry.task.child_session_id or entry.task.task_id,
            text,
            user_message_id,
        )
        if inspect.isawaitable(launch):
            launch = await launch
        entry.task.run_id = str(launch.get("run_id") or "") or None
        entry.task.assistant_message_id = str(launch.get("assistant_message_id") or "") or None
        entry.task.projection_sequence = 0
        entry.task.message_offset = baseline
    await _arun(entry, initial_source={"messages": [HumanMessage(content=text)]})


async def _arun_shell(entry: _TaskEntry) -> None:
    """kind="shell"：直接经 backend 执行命令，终态写结果与通知。

    不经 worker 编译、无 turn / followup / 审批概念；backend 的
    aexecute 即 to_thread(execute)，同步 httpx 客户端线程安全。
    终态发布（SSE + 通知）只在协程自身落终态的分支做——CancelledError
    由触发方（cancel / watchdog / 沙箱销毁）负责发布，这里不重复。
    """
    task = entry.task
    try:
        timeout = entry.shell_command_timeout
        response = await entry.shell_backend.aexecute(
            entry.shell_command or "",
            **({"timeout": timeout} if timeout is not None else {}),
        )
        task.result = _format_shell_result(response)
        task.status = BgTaskStatus.COMPLETED
        task.completed_at = time.time()
        _publish_task_event(task, "terminal")
        _notify_terminal(task)
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
            task.status = BgTaskStatus.CANCELLED
            task.completed_at = time.time()
            _publish_task_event(task, "terminal")
            _notify_terminal(task)
    except Exception as exc:
        task.status = BgTaskStatus.FAILED
        task.error = str(exc)
        task.completed_at = time.time()
        _publish_task_event(task, "terminal")
        _notify_terminal(task)
        logger.opt(exception=True).error(
            "bg shell task failed task_id={}", task.task_id,
        )


def _format_shell_result(response: Any) -> str:
    """ExecuteResponse → check_task 结果文本（exit code + 有界输出尾部）。"""
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
        _disarm_watchdog(entry)
        if entry.future is not None and not entry.future.done():
            entry.future.cancel()
        if entry.task.status.is_terminal:
            continue
        entry.task.status = BgTaskStatus.FAILED
        entry.task.error = reason
        entry.task.completed_at = time.time()
        if entry.task.run_id:
            from noesis.runtime.main_loop import run_on_main_loop
            from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService

            run_on_main_loop(
                SubagentSessionService.mark_terminal(
                    run_id=entry.task.run_id,
                    status=RunStatus.ERROR,
                    content=None,
                    error=reason,
                    finish_reason="sandbox_destroyed",
                ),
                name=f"subagent-sandbox-failed:{entry.task.run_id}",
            )
        _publish_task_event(entry.task, "terminal")
        _notify_terminal(entry.task)
    if entries:
        logger.warning(
            "bg tasks failed on session sandbox destroy session_id={} count={}",
            session_id, len(entries),
        )


def _arm_watchdog(entry: _TaskEntry) -> None:
    _disarm_watchdog(entry)
    loop = _ensure_loop()
    entry.watchdog_handle = loop.call_later(
        entry.timeout_seconds,
        _on_task_timeout,
        entry,
    )


def _schedule_entry_locked(entry: _TaskEntry) -> None:
    """把已获槽位的任务调度到执行 loop（须持 _TASKS_LOCK）。

    future 创建与 watchdog 装载必须在锁内完成：若状态置 RUNNING 后、
    future 尚未创建前被 cancel，cancel 拿不到 future 无法真正停止协程，
    任务会跑完并以 COMPLETED 覆盖 CANCELLED。run_coroutine_threadsafe /
    call_later 均为非阻塞提交，锁内调用安全；SSE 事件发布留待锁外。
    """
    loop = _ensure_loop()
    entry.future = asyncio.run_coroutine_threadsafe(_arun(entry), loop)
    if entry.timeout_seconds > 0:
        _arm_watchdog(entry)


def _publish_entry_started(entry: _TaskEntry) -> None:
    _publish_task_event(entry.task, "started")
    _publish_run_event(entry.task, "run.started")


def _dequeue_locked(task: BackgroundTask) -> None:
    """从会话排队队列移除条目（须持 _TASKS_LOCK）。"""
    queue = _PENDING_QUEUES.get(task.session_id)
    if not queue:
        return
    _PENDING_QUEUES[task.session_id] = [
        item for item in queue if item.task is not task
    ]
    if not _PENDING_QUEUES[task.session_id]:
        _PENDING_QUEUES.pop(task.session_id, None)


def _drain_session_queue(session_id: str) -> None:
    """同会话任务落终态后，按 FIFO 唤醒排队任务直到槽位占满。

    跳过排队期间已被取消/连坐的陈旧条目。在 _notify_terminal 统一触发，
    覆盖完成、失败、超时、取消、沙箱销毁全部终态路径。
    """
    while True:
        with _TASKS_LOCK:
            queue = _PENDING_QUEUES.get(session_id)
            if not queue:
                return
            entry: Optional[_TaskEntry] = None
            while queue:
                candidate = queue[0]
                if candidate.task.status != BgTaskStatus.QUEUED:
                    queue.pop(0)
                    continue
                entry = candidate
                break
            if entry is None:
                _PENDING_QUEUES.pop(session_id, None)
                return
            active = sum(
                1
                for e in _TASKS.values()
                if e.task.session_id == session_id
                and e.task.status in _SLOT_STATUSES
            )
            if active >= entry.session_max_concurrent:
                return
            queue.pop(0)
            entry.task.status = BgTaskStatus.RUNNING
            # 锁内调度（同 _launch：防 RUNNING 后 future 未建即被 cancel 的竞态）
            _schedule_entry_locked(entry)
        _publish_entry_started(entry)
        logger.info(
            "bg task dequeued task_id={} session_id={}",
            entry.task.task_id, session_id,
        )


def _disarm_watchdog(entry: _TaskEntry) -> None:
    if entry.watchdog_handle is not None:
        entry.watchdog_handle.cancel()
        entry.watchdog_handle = None


def _arm_hitl_watchdog(entry: _TaskEntry) -> None:
    """审批超时按拒绝续跑（对齐主 run HITL 超时语义）。"""
    loop = _ensure_loop()
    entry.watchdog_handle = loop.call_later(
        entry.hitl_timeout_seconds,
        _on_hitl_timeout,
        entry,
    )


def _on_task_timeout(entry: _TaskEntry) -> None:
    if (
        entry.task.status.is_terminal
        or entry.task.status == BgTaskStatus.AWAITING_APPROVAL
    ):
        return
    if entry.future is not None:
        entry.future.cancel()
    entry.task.status = BgTaskStatus.TIMED_OUT
    entry.task.error = f"后台任务超时（{int(entry.timeout_seconds)}s）"
    entry.task.completed_at = time.time()
    if entry.task.run_id:
        from noesis.runtime.main_loop import run_on_main_loop
        from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService

        run_on_main_loop(
            SubagentSessionService.mark_terminal(
                run_id=entry.task.run_id,
                status=RunStatus.ERROR,
                content=None,
                error=entry.task.error,
                finish_reason="timeout",
            ),
            name=f"subagent-timeout:{entry.task.run_id}",
        )
    _publish_task_event(entry.task, "terminal")
    _notify_terminal(entry.task)


def _on_hitl_timeout(entry: _TaskEntry) -> None:
    if entry.task.status != BgTaskStatus.AWAITING_APPROVAL:
        return
    logger.warning("bg subagent approval timeout task_id={}", entry.task.task_id)
    try:
        BackgroundSubagentExecutor.submit_decisions(
            entry.task.task_id,
            [{"type": "reject", "message": "审批超时，已自动拒绝"}],
        )
    except Exception:
        logger.opt(exception=True).error(
            "bg subagent approval timeout reject failed task_id={}",
            entry.task.task_id,
        )
def shutdown() -> None:
    """清空注册表并停掉隔离 loop（测试 / 进程退出用）。"""
    with _TASKS_LOCK:
        entries = list(_TASKS.values())
        _TASKS.clear()
        _PENDING_QUEUES.clear()
    for entry in entries:
        if entry.future is not None:
            entry.future.cancel()
    for entry in entries:
        if entry.future is not None:
            try:
                entry.future.result(timeout=2)
            except Exception:  # noqa: BLE001
                pass
    # 先在隔离 loop 内关闭其 checkpointer 连接池，再停 loop
    # （池绑定隔离 loop，停掉后无法正常关闭）
    from noesis.config.checkpointer import close_isolated_checkpointer_on_loop

    close_isolated_checkpointer_on_loop()
    shutdown_loop()


class _ExecutorRuntimePort:
    validate_followup = staticmethod(BackgroundSubagentExecutor.validate_followup)
    send_message = staticmethod(BackgroundSubagentExecutor.send_message)
    submit_decisions = staticmethod(BackgroundSubagentExecutor.submit_decisions)
    cancel = staticmethod(BackgroundSubagentExecutor.cancel)
    subscribe_run_events = staticmethod(subscribe_run_events)
    unsubscribe_run_events = staticmethod(unsubscribe_run_events)
    get_run_event_history = staticmethod(get_run_event_history)


configure_executor_port(_ExecutorRuntimePort)


__all__ = [
    "BackgroundSubagentExecutor",
    "BackgroundTask",
    "BgTaskStatus",
    "fail_session_shell_tasks",
    "shutdown",
    "shutdown_loop",
]
