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

任务元数据持久化（对齐 deepagents 教程 ch6 async_tasks channel 的诉求）：
状态转换点把快照 upsert 到注入的 task store（t_bg_task 表）；进程重启后
running/awaiting_approval 由 startup 对账标记为 failed，终态任务经
check/list 的 DB fallback 仍可查询。执行面本身（协程、future、followup
队列）仍在进程内，不跨重启恢复。
"""

from __future__ import annotations

import asyncio
import collections
import inspect
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from noesis.agents.subagents import notifications
from noesis.runtime.logging import logger

# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


class BgTaskStatus(str, Enum):
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


@dataclass
class BackgroundTask:
    """一个后台任务的公开快照（可安全序列化给 API / 工具）。"""

    task_id: str
    session_id: str
    user_id: str
    description: str
    # continuable：可经 send_message 追加 turn；one_shot：只能查看，不可续
    kind: str = "continuable"
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
            "user_id": self.user_id,
            "description": self.description,
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
    # 不得复用主 loop 创建的实例（cross-loop 风险）
    agent_factory: Callable[[], Any]
    recursion_limit: int
    timeout_seconds: float
    hitl_timeout_seconds: float
    # followup-turn 队列：send_message 入队，当前 turn 结束后链式开新 turn
    followups: "collections.deque[str]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_FOLLOWUPS),
    )
    followup_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # factory 首次调用后在隔离 loop 内缓存编译结果（同 executor 任务复用）
    compiled_agent: Any = None
    compiled_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 当前执行协程的 future（用于超时/取消）
    future: Optional[Future] = None
    watchdog_handle: Optional[asyncio.TimerHandle] = None


_TASKS: dict[str, _TaskEntry] = {}
_TASKS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 任务元数据持久化（重启后 fallback 查询 + startup 对账）
# ---------------------------------------------------------------------------


class BgTaskStore(Protocol):
    """快照存储面；具体实现见 repositories/bg_task_repository（t_bg_task 表）。"""

    def save(self, snapshot: dict[str, Any]) -> None: ...

    def get(self, task_id: str) -> Optional[dict[str, Any]]: ...

    def list_for_session(self, session_id: str) -> list[dict[str, Any]]: ...


_TASK_STORE: Optional[BgTaskStore] = None


def configure_task_store(store: BgTaskStore | None) -> None:
    """进程启动时注入（server/main.py lifespan）；测试可传内存实现。"""
    global _TASK_STORE
    _TASK_STORE = store


def _persist(task: BackgroundTask) -> None:
    """状态转换点落快照；持久化失败只记日志，绝不影响任务执行。"""
    store = _TASK_STORE
    if store is None:
        return
    try:
        store.save(task.to_dict())
    except Exception:
        logger.opt(exception=True).warning(
            "bg task snapshot persist failed task_id={}",
            task.task_id,
        )


# ---------------------------------------------------------------------------
# 会话级事件订阅（SSE push，替代前端轮询）：executor 在隔离线程发布，
# 经 call_soon_threadsafe 跨 loop 投递到订阅者的 asyncio.Queue
# ---------------------------------------------------------------------------

_BGSub = tuple[asyncio.AbstractEventLoop, asyncio.Queue, str]  # (loop, queue, user_id)
_SUBSCRIBERS: dict[str, list[_BGSub]] = {}
_SUBSCRIBERS_LOCK = threading.Lock()


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
HITL_TIMEOUT_SECONDS = 86400.0
# followup 消息上限（超出丢最旧）
MAX_FOLLOWUPS = 10
# 执行过程摘要上限（超出丢最旧）
MAX_PROGRESS_ENTRIES = 50
_PROGRESS_PREVIEW_CHARS = 120


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


def _record_step_progress(
    task: BackgroundTask, final_state: Any, seen_ids: set
) -> None:
    """从 values 快照 diff 出新增消息，记录轻量步骤摘要。

    deer-flow capture_new_step_messages 的简化版：按消息 id 去重，
    AIMessage 记文本片段/工具调用名，ToolMessage 记名称与状态。
    """
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
                _progress_append(
                    task,
                    {
                        "kind": "text",
                        "preview": text[:_PROGRESS_PREVIEW_CHARS],
                        "ts": time.time(),
                    },
                )
        elif isinstance(message, ToolMessage):
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


def _notify_terminal(task: BackgroundTask) -> None:
    """终态转换点统一记录会话通知（completed/failed/timed_out/cancelled）。"""
    notifications.record(
        session_id=task.session_id,
        task_id=task.task_id,
        status=task.status.value,
        preview=task.result or task.error,
    )
    _schedule_continuation(task)


def _schedule_continuation(task: BackgroundTask) -> None:
    """终态后尝试唤醒主 Agent（dsh parent.followup 的 run 级等价物）。

    无活跃 run 时自动创建 continuation run；调度回主 loop（DB 引擎与
    RunManager 绑定主 loop）。仅 completed 触发——失败/取消的交付由模型
    在下次交互时按通知自行决定，自动唤醒只会空转。
    """
    if task.status != BgTaskStatus.COMPLETED:
        return
    from noesis.runtime.main_loop import run_on_main_loop
    from noesis.services.bg_continuation_service import maybe_continue

    run_on_main_loop(
        maybe_continue(task.session_id, task.user_id),
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
        hitl_timeout_seconds: float = HITL_TIMEOUT_SECONDS,
        recursion_limit: int = 9999,
    ) -> None:
        self._max_concurrent = max(1, max_concurrent_per_session)
        self._task_timeout = task_timeout_seconds
        self._hitl_timeout = hitl_timeout_seconds
        self._recursion_limit = recursion_limit

    # -- 查询（任意线程安全调用） ------------------------------------

    @staticmethod
    def get(task_id: str) -> Optional[dict[str, Any]]:
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
            if entry is not None:
                return entry.task.to_dict(include_progress=False)
        # 内存 miss（典型：进程重启后）→ 持久层 fallback
        if _TASK_STORE is not None:
            try:
                return _TASK_STORE.get(task_id)
            except Exception:
                logger.opt(exception=True).warning(
                    "bg task store get failed task_id={}",
                    task_id,
                )
        return None

    @staticmethod
    def list_for_session(session_id: str) -> list[dict[str, Any]]:
        with _TASKS_LOCK:
            tasks = {
                entry.task.task_id: entry.task.to_dict(include_progress=False)
                for entry in _TASKS.values()
                if entry.task.session_id == session_id
            }
        # 合并持久层历史（内存中的活任务优先，避免读到旧快照）
        if _TASK_STORE is not None:
            try:
                for snapshot in _TASK_STORE.list_for_session(session_id):
                    tasks.setdefault(snapshot["task_id"], snapshot)
            except Exception:
                logger.opt(exception=True).warning(
                    "bg task store list failed session_id={}",
                    session_id,
                )
        return sorted(tasks.values(), key=lambda t: t["started_at"])

    @staticmethod
    def pending_approvals(session_id: str) -> list[dict[str, Any]]:
        return [
            t
            for t in BackgroundSubagentExecutor.list_for_session(session_id)
            if t["status"] == BgTaskStatus.AWAITING_APPROVAL.value
        ]

    def _session_active_count(self, session_id: str) -> int:
        with _TASKS_LOCK:
            return sum(
                1
                for entry in _TASKS.values()
                if entry.task.session_id == session_id
                and not entry.task.status.is_terminal
            )

    # -- 启动 ---------------------------------------------------------

    def start(
        self,
        *,
        worker_factory: Callable[[], Any],
        description: str,
        session_id: str,
        user_id: str,
        one_shot: bool = False,
    ) -> str:
        """启动后台任务，立即返回 task_id；超并发抛 ValueError。"""
        task_id = f"bg-{uuid.uuid4()}"
        task = BackgroundTask(
            task_id=task_id,
            session_id=session_id,
            user_id=user_id,
            description=description,
            kind="one_shot" if one_shot else "continuable",
        )
        entry = _TaskEntry(
            task=task,
            agent_factory=worker_factory,
            recursion_limit=self._recursion_limit,
            timeout_seconds=self._task_timeout,
            hitl_timeout_seconds=self._hitl_timeout,
        )
        # 上限检查与插入同锁，避免并发 start 的 TOCTOU 竞态
        with _TASKS_LOCK:
            active = sum(
                1
                for e in _TASKS.values()
                if e.task.session_id == session_id and not e.task.status.is_terminal
            )
            if active >= self._max_concurrent:
                raise ValueError(
                    f"本会话后台任务已达上限（{self._max_concurrent} 个），"
                    "请先 check/cancel 现有任务再启动新的"
                )
            _TASKS[task_id] = entry
        _persist(task)
        loop = _ensure_loop()
        entry.future = asyncio.run_coroutine_threadsafe(
            _arun(entry, resume_command=None),
            loop,
        )
        _arm_watchdog(entry)
        _publish_task_event(task, "started")
        logger.info(
            "bg subagent started task_id={} session_id={} active={}/{}",
            task_id,
            session_id,
            active + 1,
            self._max_concurrent,
        )
        return task_id

    @staticmethod
    def send_message(task_id: str, message: str) -> dict[str, Any]:
        """followup-turn：向子任务追加一个 turn。

        - running / awaiting_approval：入队，当前 turn 结束后链式开新 turn
        - completed：冷恢复——同 thread 开新 turn，任务回到 running
        - one_shot / failed / timed_out / cancelled：拒绝
        """
        text = message.strip()
        if not text:
            raise ValueError("消息不能为空")
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
            if entry is None:
                raise ValueError(f"后台任务不存在: {task_id}")
            task = entry.task
            if task.kind == "one_shot":
                raise ValueError("该任务为一次性任务，不支持追加消息")
            status = task.status
            # completed → 冷恢复：同 thread 开新 turn（followup 队列一并排入）
            if status == BgTaskStatus.COMPLETED:
                task.status = BgTaskStatus.RUNNING
                task.result = None
                task.completed_at = None
                loop = _ensure_loop()
                entry.future = asyncio.run_coroutine_threadsafe(
                    _arun(
                        entry,
                        initial_source={"messages": [HumanMessage(content=text)]},
                    ),
                    loop,
                )
                _arm_watchdog(entry)
                _persist(task)
                _publish_task_event(task, "followup")
                return task.to_dict()
            if status.is_terminal:
                raise ValueError(f"任务已结束（{status.value}），无法追加消息")
            with entry.followup_lock:
                entry.followups.append(text)
            _publish_task_event(task, "followup")
            return task.to_dict()

    @staticmethod
    def pop_followups(entry: _TaskEntry) -> list[str]:
        """取出待续 turn 消息（链式调度点消费）。"""
        with entry.followup_lock:
            messages = list(entry.followups)
            entry.followups.clear()
            return messages

    @staticmethod
    def read_messages(task_id: str) -> list[dict[str, Any]]:
        """子会话查看：只读该任务 thread 的消息历史。"""
        return read_thread_messages(task_id)

    @staticmethod
    def get_future(task_id: str) -> Optional[Future]:
        """取当前执行 future（前台等待用）。"""
        with _TASKS_LOCK:
            entry = _TASKS.get(task_id)
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
                raise ValueError(f"后台任务不存在: {task_id}")
            if entry.task.status != BgTaskStatus.AWAITING_APPROVAL:
                raise ValueError(
                    f"任务不在待审批状态（当前 {entry.task.status.value}）"
                )
            entry.task.status = BgTaskStatus.RUNNING
            entry.task.interrupt = None
        _persist(entry.task)
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
                raise ValueError(f"后台任务不存在: {task_id}")
            if entry.task.status.is_terminal:
                return entry.task.to_dict(include_progress=False)
            _disarm_watchdog(entry)
            with entry.followup_lock:
                entry.followups.clear()
            if entry.future is not None:
                entry.future.cancel()
            entry.task.status = BgTaskStatus.CANCELLED
            entry.task.completed_at = time.time()
            _persist(entry.task)
            _publish_task_event(entry.task, "terminal")
            _notify_terminal(entry.task)
            snapshot = entry.task.to_dict(include_progress=False)
        return snapshot

    # -- 内部委托模块实现（见下方模块函数） ----------------------------


def _config(entry: _TaskEntry) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": entry.task.task_id},
        "recursion_limit": entry.recursion_limit,
    }


async def _ensure_agent(entry: _TaskEntry) -> Any:
    """惰性编译 worker：factory 在隔离 loop 内调用，其 LLM 客户端 /
    checkpointer 连接池绑定隔离 loop（避免复用主 loop 实例的 cross-loop 风险）。"""
    if entry.compiled_agent is None:
        with entry.compiled_lock:
            if entry.compiled_agent is None:
                result = entry.agent_factory()
                if inspect.isawaitable(result):
                    result = await result
                entry.compiled_agent = result
    return entry.compiled_agent


def _pop_first_followup(entry: _TaskEntry) -> Optional[str]:
    with entry.followup_lock:
        return entry.followups.popleft() if entry.followups else None


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
    turn 正常结束后若 followup 队列非空，链式开下一个 turn（同 thread
    追加 HumanMessage），队列清空前任务保持 running。
    """
    task = entry.task
    try:
        agent = await _ensure_agent(entry)
        # 首轮输入：优先显式 resume command（审批续跑），
        # 否则 initial_source（start 的 description / 冷恢复的追加消息）
        source = (
            resume_command
            if resume_command is not None
            else (
                initial_source
                if initial_source is not None
                else {"messages": [HumanMessage(content=task.description)]}
            )
        )
        while True:
            seen_ids: set = set()
            final: Any = None
            # astream(values)：既拿到终态，又能逐步 diff 执行过程摘要
            async for chunk in agent.astream(
                source, _config(entry), stream_mode="values"
            ):
                final = chunk
                _record_step_progress(task, chunk, seen_ids)
            interrupts = final.get("__interrupt__") if isinstance(final, dict) else None
            payload = _extract_interrupt_payload(interrupts) if interrupts else None
            if payload is not None:
                task.status = BgTaskStatus.AWAITING_APPROVAL
                task.interrupt = payload
                _persist(task)
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
            next_message = _pop_first_followup(entry)
            if next_message is None:
                break
            task.status = BgTaskStatus.RUNNING
            task.completed_at = None
            logger.info(
                "bg subagent followup turn task_id={} queued={}",
                task.task_id,
                len(entry.followups),
            )
            source = {"messages": [HumanMessage(content=next_message)]}
        task.status = BgTaskStatus.COMPLETED
        task.completed_at = time.time()
        _persist(task)
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
            _persist(task)
    except Exception as exc:
        task.status = BgTaskStatus.FAILED
        task.error = str(exc)
        task.completed_at = time.time()
        _persist(task)
        _publish_task_event(task, "terminal")
        _notify_terminal(task)
        logger.opt(exception=True).error(
            "bg subagent failed task_id={}",
            task.task_id,
        )


def _arm_watchdog(entry: _TaskEntry) -> None:
    _disarm_watchdog(entry)
    loop = _ensure_loop()
    entry.watchdog_handle = loop.call_later(
        entry.timeout_seconds,
        _on_task_timeout,
        entry,
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
    _persist(entry.task)
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


def _message_to_view_item(message: Any) -> Optional[dict[str, Any]]:
    """LangChain 消息 → 轻量视图项（子会话查看用）。"""
    if isinstance(message, HumanMessage):
        text = (
            message.content
            if isinstance(message.content, str)
            else str(message.content)
        )
        return (
            {"role": "user", "text": text[: _PROGRESS_PREVIEW_CHARS * 2]}
            if text.strip()
            else None
        )
    if isinstance(message, AIMessage):
        calls = [
            {"name": str(call.get("name") or ""), "args": call.get("args") or {}}
            for call in (getattr(message, "tool_calls", None) or [])
        ]
        text = _final_answer_text({"messages": [message]})
        if not calls and not text.strip():
            return None
        return {
            "role": "assistant",
            "text": text[: _PROGRESS_PREVIEW_CHARS * 2],
            "tool_calls": calls,
        }
    if isinstance(message, ToolMessage):
        content = (
            message.content
            if isinstance(message.content, str)
            else str(message.content)
        )
        return {
            "role": "tool",
            "name": str(getattr(message, "name", None) or ""),
            "status": str(getattr(message, "status", None) or "success"),
            "text": content[: _PROGRESS_PREVIEW_CHARS * 2],
        }
    return None


async def _aread_thread_messages(entry: _TaskEntry) -> list[dict[str, Any]]:
    """在隔离 loop 内只读 thread 状态并映射为视图项。"""
    agent = await _ensure_agent(entry)
    state = await agent.aget_state(_config(entry))
    values = getattr(state, "values", None) or {}
    items: list[dict[str, Any]] = []
    for message in values.get("messages", []):
        item = _message_to_view_item(message)
        if item is not None:
            items.append(item)
    return items


def read_thread_messages(
    task_id: str, *, timeout: float = 10.0
) -> list[dict[str, Any]]:
    """读取后台任务子会话消息（跨线程切到隔离 loop 执行只读 aget_state）。"""
    with _TASKS_LOCK:
        entry = _TASKS.get(task_id)
        if entry is None:
            raise ValueError(f"后台任务不存在: {task_id}")
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(_aread_thread_messages(entry), loop)
    return future.result(timeout=timeout)


def shutdown() -> None:
    """清空注册表并停掉隔离 loop（测试 / 进程退出用）。"""
    with _TASKS_LOCK:
        entries = list(_TASKS.values())
        _TASKS.clear()
    for entry in entries:
        if entry.future is not None:
            entry.future.cancel()
    shutdown_loop()


__all__ = [
    "BackgroundSubagentExecutor",
    "BackgroundTask",
    "BgTaskStatus",
    "BgTaskStore",
    "configure_task_store",
    "shutdown",
    "shutdown_loop",
]
