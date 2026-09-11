"""进程内后台任务运行时（BackgroundTaskExecutor）。

承载两类后台任务，外壳共享、执行内核分开：

- ``subagent``：委派子 Agent——worker 经角色注册表解析的工厂在专用守护
  线程的隔离事件循环里惰性编译运行，产品数据由标准
  ``TChatSession/TChatMessage/TAgentRun`` 持久化（见 SubagentSessionService）；
- ``shell``：``execute`` 工具的 ``run_in_background`` 命令——不经 worker
  编译，直接经 agent backend 执行，易逝作业不持久化。

全异步 task：``start_task`` 立即返回 task_id，任务生命周期归属 session
而非主 run——主 run 结束后继续跑，任意后续轮次 ``check_task`` 收结果。
执行器类型无关：subagent 特性（worker 工厂 / followup / 落库投影）经
注入携带，状态机、并发上限、协作停止对两类任务一致。

执行面（协程、future、followup 队列）完全在进程内：注册表在内存，
进程重启即丢（接受的设计限制，启动对账收口遗留 run）。
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import copy
import inspect
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage
from noesis.chat.delivery.events import (
    RunAborted,
    RunCompleted,
    RunError,
    RunPaused,
    WireFrame,
)
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge
from noesis.chat.event_mapping.mapper import RuntimeEventMapper, new_stream_ctx
from noesis.chat.event_mapping.usage_normalize import merge_model_calls, merge_usage
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.runs import RunStatus, SubscriptionLimitExceeded
from noesis.chat.runs.delivery_bus import DeliveryCore, SequencedPayload
from noesis.agents.subagents.kinds import StopMode, behavior_of
from noesis.config.env import StreamConfig

from noesis.agents.subagents import notifications
from noesis.chat.event_mapping.retrieval import extract_deduped_sources, source_identity
from noesis.llm.finish_reason import normalize_provider_finish_reason
from noesis.llm.reasoning import get_request_reasoning_effort, set_request_reasoning_effort
from noesis.runtime.logging import logger
from noesis.runtime.stream import stream_agent_events
from noesis.services.subagent_runtime_port import configure_executor_port

# 协作停止宽限默认值（stop_grace_seconds 配置可覆盖）
STOP_GRACE_SECONDS = 30.0
# 硬杀后强制终态对账延迟：CancelledError 可能在深层执行链（langgraph/langchain/
# httpx）被吸收，_arun 的 except CancelledError 收口不保证执行。终态不能依赖
# 被取消协程的配合——宽限超时硬杀后再给协程这么多秒自行收口，仍未收口则由
# reconcile 定时器强制落终态
STOP_RECONCILE_SECONDS = 30.0


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


class BgTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
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


# 占用会话并发槽的状态：排队（QUEUED）只占队列不占槽。
# 停止是乐观终态（受理即 CANCELLED），无中间收口态占槽
_SLOT_STATUSES = frozenset({
    BgTaskStatus.RUNNING,
})


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
    # subagent 任务均可经 send_message 追加 turn；shell 任务使用独立 kind。
    kind: str = "subagent"
    # 任务的角色类型（start_task 的 subagent_type）；shell 任务为 None。
    # 投影与任务卡展示用——worker 编译配方由角色注册表在启动前解析，
    # 执行器不感知类型差异。
    subagent_type: Optional[str] = None
    # kind="shell" 的原始命令（任务详情展示执行内容用）；subagent 任务为 None
    command: Optional[str] = None
    # worker 的 model_id：上下文窗口上限解析用（主对话同源 model_limits）
    model_id: Optional[str] = None
    # 最近一次上下文快照（worker usage 提取；变更才发布/落库）
    context_snapshot: Optional[dict[str, Any]] = None
    status: BgTaskStatus = BgTaskStatus.RUNNING
    result: Optional[str] = None
    error: Optional[str] = None
    # 协作停止请求的终止原因（cancelled / timed_out）；非 None 即停止已受理
    stop_reason: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    # 步数：与 progress 分离的权威计数，口径 = 模型调用次数（与会话统计条
    # usage.steps / TTFT/步 同源）——progress 是有界预览（maxlen=50），
    # 用其长度当步数会在 50 步后封顶（所有长任务都显示「50 步」）
    step_count: int = 0
    # 执行过程摘要（有界，前端任务卡展开显示）；lock 保护跨线程读写
    progress: "collections.deque[dict[str, Any]]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_PROGRESS_ENTRIES),
    )
    progress_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 子会话检索来源（来源身份 → result dict，插入序即首见序）：终态通知与
    # check_task 携带的去重清单；完整数据以子会话落库 retrieval parts 为准
    retrieval_sources: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

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
            "subagent_type": self.subagent_type,
            "command": self.command,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            # UI 只显示步数；SSE/列表负载裁掉明细，详情走 messages API
            "progress_count": self.step_count,
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


def _loop_timer_arm(entry: _TaskEntry, attr: str, delay: float, callback) -> None:
    """在隔离 loop 线程内挂定时器（先摘旧句柄，新句柄写回 entry.<attr>）。

    cancel / start 在主线程触发挂载：直接
    ``loop.call_later`` 是跨线程 heappush——asyncio loop 非线程安全，
    与 loop 自身的堆操作竞态会破坏堆序（曾致新看门狗被提前 ~11 分钟
    弹出）。挂载统一调度
    回 loop 线程执行；摘除保持即时 cancel（TimerHandle 置标志即生效）。
    """
    loop = _ensure_loop()

    def _arm() -> None:
        prev = getattr(entry, attr)
        if prev is not None:
            prev.cancel()
        setattr(entry, attr, loop.call_later(delay, callback, entry))

    if threading.current_thread() is _loop_thread:
        _arm()
    else:
        loop.call_soon_threadsafe(_arm)


def shutdown_loop() -> None:
    """进程退出时停掉隔离 loop（FastAPI lifespan 调用）。"""
    global _loop, _loop_thread
    with _loop_lock:
        loop = _loop
        _loop = None
        _loop_thread = None
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)


def _submit_isolated(loop: asyncio.AbstractEventLoop, coro) -> Future:
    """调度协程到隔离 loop，并在干净的 contextvars 中执行。

    ``run_coroutine_threadsafe`` 经 ``call_soon_threadsafe`` 复制调用线程的
    contextvars；而调度点常在父 run 的 astream_events 追踪上下文内
    （start_task / 审批 resume 等工具执行期间）。子 Agent 若继承父
    tracer，其 LLM/工具事件会泄入父事件流——曾在父消息尾部生成幽灵
    工具 part 并触发「本轮未完成」误报。这里把真实工作放进空 Context
    的内层 Task 切断继承；取消经 await 传播，Future 语义与
    run_coroutine_threadsafe 一致。子 Agent 自身依赖（backend/
    checkpointer）均经闭包传参，不依赖 contextvars。
    """
    return asyncio.run_coroutine_threadsafe(_run_in_clean_context(coro), loop)


async def _run_in_clean_context(coro):
    task = asyncio.get_running_loop().create_task(coro, context=contextvars.Context())
    try:
        return await task
    finally:
        if not task.done():
            task.cancel()


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
    followup_factory: Optional[Callable[[str, str, Optional[str]], Any]] = None
    # 排队唤醒时按该值判断槽位（executor 实例不共享，cap 记在条目上）
    session_max_concurrent: int = 1
    # 提交序号：全局 FIFO 唤醒的排序键（跨会话公平）
    submit_seq: int = 0
    # 全局总闸快照（唤醒判定用；0 = 不限）
    max_global: int = 0
    # followup-turn 队列：send_message 入队，当前 turn 结束后链式开新 turn
    followups: "collections.deque[str]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_FOLLOWUPS),
    )
    followup_message_ids: "collections.deque[Optional[str]]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_FOLLOWUPS),
    )
    # 与 followups 逐条对应的 turn 参数（模型 / 推理档位覆盖；None = 沿用当前）
    followup_turn_params: "collections.deque[Optional[_TurnParams]]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_FOLLOWUPS),
    )
    # 生效中的模型覆盖：非 None 时 _ensure_agent 以该模型重新编译 worker
    model_override: Optional[str] = None
    # 生效中的推理档位（turn 级；LLM 构造时经 ContextVar 固化为请求参数）。
    # 创建时在父 run 上下文捕获（后台 worker 隔离 loop 干净上下文拿不到父档位）
    turn_reasoning_effort: Optional[str] = None
    followup_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # factory 首次调用后在隔离 loop 内缓存编译结果（同 executor 任务复用）
    compiled_agent: Any = None
    compiled_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 当前执行协程的 future（用于超时/取消）
    future: Optional[Future] = None
    watchdog_handle: Optional[asyncio.TimerHandle] = None
    # 协作停止信号：cancel 受理时置位（线程同步可见），执行循环在静止边界
    # 观察退出。停止已是乐观终态（受理即落 CANCELLED），该信号只驱动执行侧
    # 干净退出，不构成对外状态
    cooperative_stop_signalled: bool = False
    # 协作停止宽限 watchdog（超时回退硬杀）
    stop_grace_handle: Optional[asyncio.TimerHandle] = None
    # 硬杀后强制终态对账 watchdog（协程未按约收口时兜底）
    stop_reconcile_handle: Optional[asyncio.TimerHandle] = None
    # 对账兜底协程的强引用（防 GC 回收未完成的 task）
    stop_reconcile_task: Optional[asyncio.Task] = None
    # 终态副作用（run.finished / terminal 事件 / 通知 / drain）归属：
    # 首个置位者负责发布，_finalize_stop 与 _force_terminal 竞争时只发一次
    terminal_published: bool = False
    # 已完成 turn 的 usage 累计（数值字段相加）：实时统计发布时与当前
    # turn 的 bridge.message_usage 合并，保证跨轮口径与终态 DB 重建一致
    accumulated_usage: Optional[dict[str, Any]] = None
    # 协作停止宽限（秒）：executor 实例配置
    stop_grace_seconds: float = STOP_GRACE_SECONDS
    # 硬杀后强制终态对账延迟（秒）：executor 实例配置
    stop_reconcile_seconds: float = STOP_RECONCILE_SECONDS
    # kind="shell"：执行 backend（local_shell 宿主机 / docker 容器）；
    # 命令本体在 task.command（展示与执行同源）
    shell_backend: Any = None
    # 命令级超时（None=不向 backend 传 timeout，走 backend 默认）
    shell_command_timeout: Optional[int] = None


_TASKS: dict[str, _TaskEntry] = {}
_TASKS_LOCK = threading.Lock()
# 会话级排队任务（超出并发上限时 FIFO 等待，不占并发槽、不启动 watchdog）
_PENDING_QUEUES: dict[str, list[_TaskEntry]] = {}


# 任务不存在时的统一提示：模型惯用短 id，指路 list_tasks 避免盲试
_TASK_NOT_FOUND = "后台任务不存在: {task_id}（可用 list_tasks 查看完整 task_id）"


def _find_entry_locked(task_id: str) -> Optional[_TaskEntry]:
    """按 task_id / child_session_id 查找任务（须持 _TASKS_LOCK）。

    支持唯一前缀匹配（git 短哈希语义）：模型在表格里惯用 8 位短 id，
    只做精确匹配会让 cancel/check 全部落空（曾在用户要求停止时整批
    「后台任务不存在」而任务照跑）。前缀命中多个时返回 None，由调用
    方按不存在处理——歧义 id 不猜测。
    """
    key = str(task_id or "").strip()
    if not key:
        return None
    if key in _TASKS:
        return _TASKS[key]
    matches = [
        entry
        for entry in _TASKS.values()
        if str(entry.task.child_session_id or "").startswith(key)
        or entry.task.task_id.startswith(key)
    ]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# 会话级事件订阅（SSE push，替代前端轮询）：executor 在隔离线程发布，
# 经 call_soon_threadsafe 跨 loop 投递到订阅者的 asyncio.Queue
# ---------------------------------------------------------------------------

_BGSub = tuple[asyncio.AbstractEventLoop, asyncio.Queue, str]  # (loop, queue, user_id)
_SUBSCRIBERS: dict[str, list[_BGSub]] = {}
_SUBSCRIBERS_LOCK = threading.Lock()
# 子会话 run 事件投递：统一投递内核实例注册表（按 run_id 持有，语义实现
# 在 DeliveryCore 单点）。缓存上限与订阅配额与主链路同一份 StreamConfig。
_RUN_DELIVERY: dict[str, DeliveryCore] = {}
_RUN_DELIVERY_LOCK = threading.Lock()


def _delivery_core(run_id: str) -> DeliveryCore:
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
        if core is None:
            core = DeliveryCore(
                max_buffer_events=StreamConfig.run_event_buffer_max_events,
                max_buffer_bytes=StreamConfig.run_event_buffer_max_bytes,
            )
            _RUN_DELIVERY[run_id] = core
        return core


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
    """按标准 AgentRun 订阅 child session 事件（详情打开时使用）。

    per-run 订阅上限与主链路同一份配置（超限抛 SubscriptionLimitExceeded，
    端点映射 429）。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
        if core is None:
            core = DeliveryCore(
                max_buffer_events=StreamConfig.run_event_buffer_max_events,
                max_buffer_bytes=StreamConfig.run_event_buffer_max_bytes,
            )
            _RUN_DELIVERY[run_id] = core
        if len(core.subscribers) >= StreamConfig.run_max_subscriptions_per_run:
            raise SubscriptionLimitExceeded(
                f"per-run subscription limit exceeded: run={run_id} "
                f"max={StreamConfig.run_max_subscriptions_per_run}"
            )
        core.subscribers.append((loop, queue, user_id))
    return queue


def unsubscribe_run_events(run_id: str, queue: asyncio.Queue) -> None:
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
        if core is not None:
            core.subscribers = [s for s in core.subscribers if s[1] is not queue]


def get_run_event_history(run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
    """按投递内核重放：断档只发快照（首帧 run-snapshot），不假装连续补齐。

    首连（after_sequence<=0）只放行 run.started——内容恢复走首帧快照。
    """
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
    if core is None:
        return []
    if after_sequence <= 0:
        return [
            item.payload for item in core.buffer
            if isinstance(item.payload, dict) and item.payload.get("type") == "run.started"
        ]
    replay, snapshot_required = core.replay_after(after_sequence)
    if snapshot_required:
        return []
    return [item.payload for item in replay]


def _put_run_subscriber(sub: "_BGSub", payload: dict[str, Any]) -> None:
    """跨 loop 投递到订阅队列（满则丢弃——重连方由快照+重放恢复）。"""
    loop, queue, _user = sub

    def _put(q: asyncio.Queue = queue, p: dict[str, Any] = payload) -> None:
        try:
            q.put_nowait(p)
        except asyncio.QueueFull:
            pass

    try:
        loop.call_soon_threadsafe(_put)
    except RuntimeError:
        pass


def _publish_run_event(
    task: BackgroundTask,
    event: str,
    *,
    content: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    wire: Optional[dict[str, Any]] = None,
    transient: bool = False,
    sequence: Optional[int] = None,
) -> None:
    """发布子会话 run 事件（统一投递内核：重放缓存 + 在线订阅双通道）。

    wire：桥接层 wire 帧字段（text-delta 等），原样并入 payload。
    transient：瞬态事件（流式 delta / 实时统计）——只发在线订阅、不占
    sequence、不进缓存：重连方由 run-snapshot 全量内容恢复，回放叠加旧
    delta 反而重复。durable 事件的 sequence 由内核分配（投影事件可经
    ``sequence=`` 显式指定，与投影落库 guard 同号——投递与 DB 一个数空间）。
    """
    if not task.run_id:
        return
    core = _delivery_core(task.run_id)
    with _RUN_DELIVERY_LOCK:
        if transient:
            payload = {
                "type": event,
                "run_id": task.run_id,
                "session_id": task.child_session_id or task.task_id,
                "sequence": core.next_sequence - 1,
                "status": task.status.value,
                "transient": True,
            }
        else:
            if sequence is None:
                sequence = core.assign_sequence()
            payload = {
                "type": event,
                "run_id": task.run_id,
                "session_id": task.child_session_id or task.task_id,
                "sequence": sequence,
                "status": task.status.value,
            }
        if wire is not None:
            payload.update(wire)
        if finish_reason:
            payload["finish_reason"] = finish_reason
        # 终态时间：前端据此冻结 duration（重放历史事件同样可得）
        if task.completed_at is not None:
            payload["finished_at"] = task.completed_at
        if content is not None:
            payload["content"] = content
        if context is not None:
            payload["context"] = context
        subscribers = list(core.subscribers)
        if not transient:
            core.commit(SequencedPayload(sequence, payload))
    for sub in subscribers:
        if task.user_id not in (None, sub[2]):
            continue
        _put_run_subscriber(sub, payload)
    if event == "run.finished":
        def _expire_delivery(run_id: str = task.run_id) -> None:
            with _RUN_DELIVERY_LOCK:
                _RUN_DELIVERY.pop(run_id, None)

        timer = threading.Timer(300.0, _expire_delivery)
        timer.daemon = True
        timer.start()
    # 父会话只接收摘要目录更新；正文仍只在 child drawer 打开时订阅 run SSE。
    if task.child_session_id:
        from noesis.services.subagent_runtime_port import child_session_summary

        publish_session_event(
            task.session_id,
            task.user_id,
            {
                "event": "child-session",
                "child": child_session_summary(
                    task.to_dict(include_progress=False), parent_id=task.session_id,
                ),
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


def _progress_append(task: BackgroundTask, entry: dict[str, Any]) -> None:
    with task.progress_lock:
        task.progress.append(entry)


def _apply_context_snapshot(task: BackgroundTask, snapshot: dict[str, Any]) -> None:
    """统一管道 context-update 帧驱动：变更才发布 + 落库子会话 extra.context。

    与主对话同口径（usage.input_tokens 单轮真实值、每次覆盖）——
    快照提取已收敛到 bridge 的模型调用边界，executor 不再自行提取。
    """
    if not task.child_session_id:
        return
    if snapshot == getattr(task, "context_snapshot", None):
        return
    task.context_snapshot = dict(snapshot)
    _publish_run_event(task, "context-update", context=dict(snapshot))
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


def _record_progress_from_model_end(task: BackgroundTask, message: AIMessage) -> str:
    """on_chat_model_end 边界的进度摘要：步数 +1 + 工具调用/文本预览。

    步数口径 = 模型调用次数，与子会话统计条 usage.steps（「N 轮 · M 步」、
    TTFT/步）同源；tool_calls / text / tool_result 只进预览不计步。
    返回本条消息的可见文本（task.result 口径）。
    """
    with task.progress_lock:
        task.step_count += 1
    text = _child_message_text(message)
    for call in getattr(message, "tool_calls", None) or []:
        _progress_append(
            task,
            {"kind": "tool_call", "name": str(call.get("name") or ""), "ts": time.time()},
        )
    if text.strip():
        _progress_append(
            task,
            {"kind": "text", "preview": text[:_PROGRESS_PREVIEW_CHARS], "ts": time.time()},
        )
    return text


def _record_progress_from_tool_end(task: BackgroundTask, message: Any) -> str:
    """on_tool_end 边界的进度摘要（工具结果预览，不计步）。"""
    text = _child_message_text(message)
    _progress_append(
        task,
        {
            "kind": "tool_result",
            "name": str(getattr(message, "name", None) or ""),
            "status": str(getattr(message, "status", None) or "success"),
            "preview": text[:_PROGRESS_PREVIEW_CHARS],
            "ts": time.time(),
        },
    )
    return text


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


def _final_model_fallback_error(message: Optional[AIMessage]) -> Optional[str]:
    """最后一次模型调用若为 LLM 降级失败说明，返回其文本。

    middleware 重试耗尽会返回 content 为失败文案的 AIMessage（带
    noesis_model_fallback 标记）；此时 run 不能标 completed——否则父 Agent
    会把「服务暂时不可用」当作子任务产出。
    """
    if message is None:
        return None
    from noesis.agents.middlewares.llm_error_handling_middleware import is_model_fallback_message

    if is_model_fallback_message(message):
        return _child_message_text(message) or "模型服务暂不可用"
    return None


async def _projection_boundary(task: BackgroundTask, builder: AssistantMessageBuilder) -> None:
    """投影边界统一收口：任务级来源合并（无条件）+ 子会话投影落库（有标准 run 时）。"""
    content = builder.to_dict()
    _merge_task_sources(task, content)
    if task.run_id:
        await _persist_child_projection(task, content)


def _merge_task_sources(task: BackgroundTask, content: dict[str, Any]) -> None:
    """投影内容中的 retrieval parts → 任务级去重来源清单（幂等合并）。"""
    for item in extract_deduped_sources(content):
        identity = source_identity(item)
        if identity and identity not in task.retrieval_sources:
            task.retrieval_sources[identity] = item


async def _persist_child_projection(
    task: BackgroundTask,
    content: dict[str, Any],
) -> None:
    """子会话投影落库（content 为统一管道 builder 产物）。

    投影序号 = 投递内核已提交的最后一帧序号（边界时 builder 内容与全部
    已提交帧一致）——DB guard 与投递事件同一个数空间，且不产生缓存空洞。
    内容恢复走 run-snapshot 快照 + 帧重放（message.updated 退役）。
    """
    if not task.run_id or not task.assistant_message_id:
        return
    core = _delivery_core(task.run_id)
    with _RUN_DELIVERY_LOCK:
        sequence = core.next_sequence - 1
    task.projection_sequence = sequence
    from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
    from noesis.runtime.main_loop import run_on_main_loop

    future = run_on_main_loop(
        SubagentSessionService.persist_projection(
            run_id=task.run_id,
            assistant_message_id=task.assistant_message_id,
            content=copy.deepcopy(content),
            sequence=sequence,
        ),
        name=f"subagent-projection:{task.run_id}:{sequence}",
    )
    if future is not None:
        await asyncio.wrap_future(future)


def _notify_preview(task: BackgroundTask) -> Optional[str]:
    """通知预览：取消/超时携带部分产出内容本身（标注前缀不占预览预算）。"""
    result = task.result
    if result and result.startswith(_PARTIAL_OUTPUT_PREFIX):
        return result[len(_PARTIAL_OUTPUT_PREFIX):].lstrip() or None
    return result or task.error


def _notify_terminal(task: BackgroundTask) -> None:
    """终态转换点统一记录会话通知（completed/failed/timed_out/cancelled）。"""
    notifications.record(
        session_id=task.session_id,
        task_id=task.child_session_id or task.task_id,
        status=task.status.value,
        preview=_notify_preview(task),
        label=task.description,
        sources=list(task.retrieval_sources.values()),
        step_count=task.step_count,
        turn_count=task.turn_count if behavior_of(task.kind).has_turns else None,
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


class BackgroundTaskExecutor:
    """start/check/cancel/list 的进程内执行面。"""

    def __init__(
        self,
        *,
        max_concurrent_per_session: int = MAX_CONCURRENT_PER_SESSION,
        max_concurrent_global: int = 0,
        task_timeout_seconds: float = TASK_TIMEOUT_SECONDS,
        shell_task_timeout_seconds: float = SHELL_TASK_TIMEOUT_SECONDS,
        stop_grace_seconds: float = STOP_GRACE_SECONDS,
        stop_reconcile_seconds: float = STOP_RECONCILE_SECONDS,
        recursion_limit: int = 9999,
    ) -> None:
        self._max_concurrent = max(1, max_concurrent_per_session)
        # 全局并发总闸（跨会话）：0 = 不限。两级准入先全局后会话，
        # 全局按提交序 FIFO 唤醒（防单会话占满总闸饿死其他会话）
        self._max_global = max(0, max_concurrent_global)
        self._task_timeout = task_timeout_seconds
        self._shell_timeout = max(0.0, shell_task_timeout_seconds)
        self._stop_grace = max(1.0, stop_grace_seconds)
        self._stop_reconcile = max(1.0, stop_reconcile_seconds)
        self._recursion_limit = recursion_limit

    # -- 查询（任意线程安全调用） ------------------------------------

    @staticmethod
    def get(task_id: str) -> Optional[dict[str, Any]]:
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
            if entry is not None:
                return entry.task.to_dict(include_progress=False)
        return None

    @staticmethod
    def sources_of(task_id: str) -> list[dict[str, Any]]:
        """任务级去重来源清单（跨边界传递用；check_task / 通知携带）。"""
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
            return list(entry.task.retrieval_sources.values()) if entry else []

    @staticmethod
    def get_memory(task_id: str) -> Optional[dict[str, Any]]:
        """只查进程内注册表，供 async catalog 避免同步数据库 fallback。"""
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
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
        subagent_type: Optional[str] = None,
        kind: str = "subagent",
    ) -> str:
        """启动后台任务，立即返回 task_id；超并发上限时按会话 FIFO 排队。

        description = 简短标题（任务卡/列表展示）；prompt = 完整任务指令
        （子 Agent 首轮输入，缺省回退 description）。worker 编译配方由
        调用方（角色注册表）解析为 worker_factory 注入——执行器类型无关。
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
            kind=kind,
            model_id=model_id,
            subagent_type=subagent_type,
        )
        entry = _TaskEntry(
            task=task,
            agent_factory=worker_factory,
            followup_factory=followup_factory,
            recursion_limit=self._recursion_limit,
            timeout_seconds=self._task_timeout,
            # 创建时档位继承：start 在父 run 上下文调用（ContextVar 可见）；
            # worker 在隔离 loop 编译前经 _arun 显式设置回该档位
            turn_reasoning_effort=get_request_reasoning_effort(),
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
        时），并发上限与状态机复用。

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
            command=command,
        )
        entry = _TaskEntry(
            task=task,
            agent_factory=None,
            recursion_limit=self._recursion_limit,
            timeout_seconds=self._shell_timeout,
            shell_backend=backend,
            shell_command_timeout=(
                timeout if timeout is not None else _SHELL_DEFAULT_COMMAND_TIMEOUT
            ),
        )
        self._launch(entry)
        return task_id

    def _launch(self, entry: _TaskEntry) -> None:
        """并发预检 + 插入注册表 + 调度执行（subagent / shell 同一入口）。
        stop_grace_seconds 在此统一注入（实例配置）。

        超上限不再拒绝：任务置 QUEUED 按会话 FIFO 排队，任一同会话任务落
        终态后由 _drain_session_queue 调度。排队等待不占并发槽、不启动
        watchdog（900s 预算从实际开始执行起算）。上限检查与插入同锁，
        避免并发 start 的 TOCTOU 竞态。
        """
        task = entry.task
        session_id = task.session_id
        entry.session_max_concurrent = self._max_concurrent
        entry.max_global = self._max_global
        entry.stop_grace_seconds = self._stop_grace
        entry.stop_reconcile_seconds = self._stop_reconcile
        with _TASKS_LOCK:
            active = sum(
                1
                for e in _TASKS.values()
                if e.task.session_id == session_id
                and e.task.status in _SLOT_STATUSES
            )
            global_active = sum(
                1
                for e in _TASKS.values()
                if e.task.status in _SLOT_STATUSES
            )
            _TASKS[task.task_id] = entry
            entry.submit_seq = _next_submit_seq()
            if active >= self._max_concurrent or (
                self._max_global > 0 and global_active >= self._max_global
            ):
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
    async def deliver_followup(
        task_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> dict[str, Any]:
        """单一异步 followup 入口：校验 + 入队 / 冷恢复。

        校验（能力门控 + 终态资格）在锁内前置完成；冷恢复分支在返回前
        完成新 run 创建（run_id 权威）——响应携带旧 run_id 会让订阅方
        错过新 run 全部事件。运行中任务入队，当前 turn 结束后链式执行。
        """
        text = message.strip()
        if not text:
            raise ValueError("消息不能为空")
        params = _TurnParams(model_id=model_id, reasoning_effort=reasoning_effort)
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
            if entry is None:
                raise ValueError(_TASK_NOT_FOUND.format(task_id=task_id))
            task = entry.task
            if not behavior_of(task.kind).supports_followup:
                raise ValueError(behavior_of(task.kind).reject_followup_text())
            if task.status not in _RESUMABLE_TERMINALS:
                if task.status.is_terminal:
                    raise ValueError(f"任务已结束（{task.status.value}），无法追加消息")
                with entry.followup_lock:
                    entry.followups.append(text)
                    entry.followup_message_ids.append(user_message_id)
                    entry.followup_turn_params.append(params)
                _publish_task_event(task, "followup")
                return task.to_dict()
            # 先占位 RUNNING：run 创建窗口内受理的停止由宽限对账兜底
            task.status = BgTaskStatus.RUNNING
            task.result = None
            task.completed_at = None
            # 复活中和（与同步版同款）：清停止信号、取消旧协程与在飞对账
            # task、重置 terminal_published（复活轮发自己的终态事件与通知）
            entry.cooperative_stop_signalled = False
            if entry.future is not None and not entry.future.done():
                entry.future.cancel()
            entry.future = None
            if entry.stop_reconcile_task is not None and not entry.stop_reconcile_task.done():
                entry.stop_reconcile_task.cancel()
            entry.stop_reconcile_task = None
            entry.terminal_published = False
            _disarm_terminal_timers(entry)
        if entry.followup_factory is None:
            loop = _ensure_loop()
            entry.future = _submit_isolated(
                loop, _arun_followup(entry, text, user_message_id, params),
            )
            _arm_watchdog(entry)
            _publish_task_event(task, "followup")
            return task.to_dict()
        try:
            _apply_turn_params(entry, params)
            task.turn_count += 1
            launch = entry.followup_factory(
                task.child_session_id or task.task_id, text, user_message_id,
            )
            if inspect.isawaitable(launch):
                launch = await launch
        except Exception as exc:
            await _finalize_followup_prelude_failure(entry, task, exc)
            return task.to_dict()
        with _TASKS_LOCK:
            task.run_id = str(launch.get("run_id") or "") or None
            task.assistant_message_id = str(launch.get("assistant_message_id") or "") or None
            task.projection_sequence = 0
            # 创建窗口内已受理停止：不提交执行——宽限 watchdog 对账时
            # task.run_id 已是新 run，终态化正确收口
            stopped_during_launch = entry.cooperative_stop_signalled
        if not stopped_during_launch:
            loop = _ensure_loop()
            entry.future = _submit_isolated(
                loop,
                _arun(entry, initial_source={"messages": [HumanMessage(content=text)]}),
            )
            _arm_watchdog(entry)
        _publish_task_event(task, "followup")
        return task.to_dict()

    @staticmethod
    def pop_followups(entry: _TaskEntry) -> list[str]:
        """取出待续 turn 消息（链式调度点消费）。"""
        with entry.followup_lock:
            messages = list(entry.followups)
            entry.followups.clear()
            entry.followup_message_ids.clear()
            entry.followup_turn_params.clear()
            return messages

    @staticmethod
    def get_future(task_id: str) -> Optional[Future]:
        """取当前执行 future（前台等待用）。"""
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
            return entry.future if entry else None

    # -- 取消 ---------------------------------------------------------

    @staticmethod
    def cancel(task_id: str) -> dict[str, Any]:
        """请求停止一个后台任务：乐观终态，对齐主 Agent 停止语义。

        - 受理即落 CANCELLED 终态（UI 同步停止），协程取消 fire-and-forget
        - 部分成果回收与终态通知在后台异步完成：投影回收（跨 loop DB 往返，
          大投影可达秒级）不挡受理路径；通知在回收完成后发送（含部分成果）
        - 已终态幂等返回；回收失败由对账 watchdog 兜底（通知降级发送）
        - queued：无进行中的步骤，即时终态
        """
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
            if entry is None:
                raise ValueError(_TASK_NOT_FOUND.format(task_id=task_id))
            task = entry.task
            if task.status.is_terminal:
                return task.to_dict(include_progress=False)
            if task.status == BgTaskStatus.RUNNING and (
                behavior_of(task.kind).request_stop(entry) == StopMode.COOPERATIVE
            ):
                # 乐观终态：状态直接落 CANCELLED（快照立即对前端生效）；
                # 执行侧经协作停止信号在静止边界干净退出，收口异步补
                # 投影回收与通知。宽限/对账 watchdog 保持武装：异步收口
                # 卡死时对账兜底（此时终态已落，只补通知与落库）
                task.status = BgTaskStatus.CANCELLED
                task.stop_reason = "cancelled"
                task.completed_at = time.time()
                entry.cooperative_stop_signalled = True
                _arm_stop_grace(entry)
                snapshot = task.to_dict(include_progress=False)
            else:
                # queued（无执行 future）/ IMMEDIATE_CANCEL（命令在 backend
                # 不可中断，无协作边界）：即时终态
                _disarm_watchdog(entry)
                if task.status == BgTaskStatus.QUEUED:
                    _dequeue_locked(task)
                if entry.future is not None:
                    entry.future.cancel()
                task.status = BgTaskStatus.CANCELLED
                task.stop_reason = "cancelled"
                task.completed_at = time.time()
                snapshot = task.to_dict(include_progress=False)
        # 锁外发布：drain / 终态通知需要再拿 _TASKS_LOCK
        if not entry.cooperative_stop_signalled:
            # 即时终态（锁内已置状态供快照返回）：收口只补落库与事件
            _finalize_task_sync(entry, _stop_terminal(entry))
        # RUNNING 协作停止：终态事件与通知由执行协程的静止边界收口发布
        # （携带完整 outcome 的部分成果回收）；宽限超时经硬杀的 CancelledError
        # 路径收口（outcome=None，进度摘要回收），对账 watchdog 兜底不依赖
        # 协程配合——cancel 不自起收口协程，避免与执行侧收口竞争 outcome
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
    编译前设置本 turn 推理档位——档位在 LLM 构造时经 ContextVar 固化为
    请求参数，这里是所有编译路径（首轮/followup 切参/审批 resume）的唯一收口。
    """
    if entry.compiled_agent is None:
        with entry.compiled_lock:
            if entry.compiled_agent is None:
                set_request_reasoning_effort(entry.turn_reasoning_effort)
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


@dataclass
class _TurnParams:
    """followup turn 的执行参数：模型与推理档位均逐 turn 覆盖。"""

    model_id: Optional[str] = None
    reasoning_effort: Optional[str] = None


def _apply_turn_params(entry: _TaskEntry, params: Optional[_TurnParams]) -> bool:
    """应用 turn 参数：模型或推理档位任一变化即失效已编译 worker。

    档位在 LLM 构造时（factory 读 ContextVar）固化为请求参数，因此
    档位变化与模型变化一样需要重编译 worker（同 thread 续跑，历史保留）。
    字段缺省（None）= 沿用当前值，不视为覆盖（与 model_id 语义一致）：
    followup 未指定档位时继承任务创建时捕获的档位。
    """
    if params is None:
        return False
    changed = _apply_model_override(entry, params.model_id)
    if (
        params.reasoning_effort is not None
        and params.reasoning_effort != entry.turn_reasoning_effort
    ):
        entry.turn_reasoning_effort = params.reasoning_effort
        with entry.compiled_lock:
            entry.compiled_agent = None
        changed = True
    return changed


def _pop_first_followup(entry: _TaskEntry) -> Optional[tuple[str, Optional[str], _TurnParams]]:
    with entry.followup_lock:
        if not entry.followups:
            return None
        text = entry.followups.popleft()
        message_id = entry.followup_message_ids.popleft() if entry.followup_message_ids else None
        params = (
            entry.followup_turn_params.popleft()
            if entry.followup_turn_params else None
        )
        return text, message_id, params


@dataclass
class _TurnOutcome:
    """统一管道单 turn 的结果：驱动 executor 的终态 / followup 决策。"""

    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=dict)
    # 本 turn 每次模型调用明细（RunCompleted.model_calls 捕获），
    # 终态随 usage 一起落 message.extra.model_calls
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    # RunError 的用户可见消息（流异常经管道产出，等价旧值的异常路径）
    error_message: Optional[str] = None
    # 最后一次模型调用为 LLM 降级失败说明（终态不得标 completed）
    fallback_error: Optional[str] = None
    # 本 turn 最后一段可见文本（task.result 口径：倒序最后一条带文本消息）
    final_text: str = ""
    # 本 turn 的标准 multipart 投影（统一管道 builder 产物）
    content: dict[str, Any] = field(default_factory=lambda: {"version": 1, "parts": []})
    # 协作停止：停止请求已在静止边界受理，本 turn 提前退出走取消收尾
    cooperative_stop: bool = False
    # 输出截断（provider finish_reason=length）：本轮终态不得标 completed
    truncated: bool = False


class _TurnPipelineError(Exception):
    """统一管道报告的流错误（走 _arun 的既有异常收尾路径）。

    携带出错 turn 的 usage / model_calls：失败终态此前不落 usage，子会话
    统计条在失败后无数据可重建（只能靠实时统计兜底）——错误发生前模型
    调用的真实累计随终态一并落库。
    """

    def __init__(
        self,
        message: str,
        *,
        usage: Optional[dict[str, Any]] = None,
        model_calls: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.model_calls = model_calls


# 协作停止收尾标注：前缀只出现在 task.result / check_task 全文，不占通知预览预算
_PARTIAL_OUTPUT_PREFIX = "中止前部分产出"
_PARTIAL_RESULT_MAX_CHARS = 4000


def _try_transition(task: BackgroundTask, next_status: BgTaskStatus) -> bool:
    """非终态状态写入收口（RUNNING 恢复）：
    终态不得被覆写（乐观停止受理即落 CANCELLED，执行侧的恢复/
    followup 写入不得复活已停任务）。

    执行侧恢复/审批写入经此在同一把锁下复查——互斥关闭「检查后写入」
    窗口（否则停止被覆写丢失，followup 甚至反向新开 run）。
    返回 False = 任务已终态（多为停止受理），调用方走取消收尾。
    """
    with _TASKS_LOCK:
        if task.status.is_terminal:
            return False
        task.status = next_status
        return True


def _turn_run_status(outcome: "_TurnOutcome", fallback_error: Optional[str]) -> RunStatus:
    """turn 终态：降级失败 → ERROR；截断 → PARTIAL；否则 COMPLETED。"""
    status, _ = _fallback_terminal(fallback_error)
    if outcome.truncated and not fallback_error:
        status = RunStatus.PARTIAL
    return status


def _turn_finish_reason(outcome: "_TurnOutcome", fallback_error: Optional[str]) -> str:
    """turn finish_reason：error > truncated > 管道值（单处合成，两调用点共享）。"""
    if fallback_error:
        return "error"
    if outcome.truncated:
        return "truncated"
    return outcome.finish_reason or "stop"


def _turn_text_parts(outcome: "_TurnOutcome") -> str:
    """当前 turn 投影的全部 text parts（协作退出时的兜底提取）。"""
    return "\n".join(
        str(part.get("content") or "")
        for part in outcome.content.get("parts", [])
        if part.get("type") == "text" and part.get("content")
    ).strip()


async def _collect_persisted_text(task: BackgroundTask) -> str:
    """从子会话全部 assistant 消息投影提取 text parts（部分成果的权威来源）。

    覆盖全部轮次（followup 链早轮）与硬杀场景（最后一次边界 persist 的投影）；
    无标准 run（测试/无 run_id）由调用方退回 turn 投影兜底。任何失败降级为空
    （spec：不阻塞终止）——包括端口缺方法的 AttributeError：该协程构造期
    同步抛出，必须整体包裹，否则会炸穿 _finalize_stop 使 run 永久 RUNNING。
    """
    if not (task.child_session_id and task.run_id):
        return ""
    try:
        from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
        from noesis.runtime.main_loop import run_on_main_loop

        future = run_on_main_loop(
            SubagentSessionService.collect_partial_output(
                task.child_session_id, task.user_id,
            ),
            name=f"subagent-partial:{task.run_id}",
        )
        if future is None:
            return ""
        return (await asyncio.wrap_future(future)) or ""
    except Exception:
        # 提取失败降级为空；调用方兜底（turn 投影）
        logger.opt(exception=True).warning(
            "bg subagent partial output collect failed task_id={}", task.task_id,
        )
        return ""


_STOP_TERMINALS: frozenset[BgTaskStatus] = frozenset(
    {BgTaskStatus.CANCELLED, BgTaskStatus.TIMED_OUT}
)

# 可冷恢复续聊的终态：停止是乐观终态且只终止执行（执行/意图分离），
# 排队与后续的 followup 意图保留——completed / cancelled 均可经
# send_message 同 thread 续跑；failed / timed_out 语义上不可续
_RESUMABLE_TERMINALS: frozenset[BgTaskStatus] = frozenset(
    {BgTaskStatus.COMPLETED, BgTaskStatus.CANCELLED}
)


@dataclass(frozen=True)
class TaskTerminal:
    """终态规格：一条收口路径一份规格，落库/事件/通知语义集中在 _finalize_task。

    task_status / run_status / finish_reason 三元组决定终态语义；
    content=None 表示沿用 run 已积累快照（硬杀 / 无投影场景）。
    """

    task_status: BgTaskStatus
    run_status: RunStatus
    finish_reason: str
    error: Optional[str] = None
    content: Optional[dict[str, Any]] = None
    usage: Optional[dict[str, Any]] = None
    model_calls: Optional[list[dict[str, Any]]] = None
    stop_reason: Optional[str] = None


def _stop_terminal(entry: _TaskEntry) -> TaskTerminal:
    """停止族终态规格（cancelled / timed_out → run PARTIAL）。"""
    task = entry.task
    reason = task.stop_reason or "cancelled"
    if reason == "timed_out":
        return TaskTerminal(
            task_status=BgTaskStatus.TIMED_OUT,
            run_status=RunStatus.PARTIAL,
            finish_reason="timeout",
            error=f"后台任务超时（{int(entry.timeout_seconds)}s）",
            stop_reason=reason,
        )
    return TaskTerminal(
        task_status=BgTaskStatus.CANCELLED,
        run_status=RunStatus.PARTIAL,
        finish_reason=reason,
        error=task.error or "任务已取消",
        stop_reason=reason,
    )


def _disarm_terminal_timers(entry: _TaskEntry) -> None:
    """终态定时器统一拆除：watchdog / 停止宽限 / 硬杀对账。"""
    _disarm_watchdog(entry)
    _disarm_stop_grace(entry)
    _disarm_stop_reconcile(entry)


def _accept_terminal(entry: _TaskEntry, terminal: TaskTerminal) -> Optional[TaskTerminal]:
    """持锁受理终态：规格归一化 + 状态写入。

    停止为乐观终态（cancel 受理即落 CANCELLED）：执行侧静止边界到达的
    停止族规格不再分流拒绝，与既有终态一致走「晚到规格降级保留载荷」。
    归一化与写入必须同锁完成：sync 收口（主线程）与 async 收口（隔离
    loop）跨线程并发时，锁外的「先归一化后写入」会以过期状态决策，
    破坏先到终态语义获胜的约束。
    """
    task = entry.task
    with _TASKS_LOCK:
        if task.status.is_terminal:
            if task.status != terminal.task_status and task.status in _STOP_TERMINALS:
                # 先到的停止终态获胜：晚到规格降级为停止语义，仅保留载荷
                terminal = replace(
                    _stop_terminal(entry),
                    content=terminal.content,
                    usage=terminal.usage,
                    model_calls=terminal.model_calls,
                )
        else:
            task.status = terminal.task_status
            if terminal.error is not None:
                task.error = terminal.error
            if terminal.stop_reason is not None:
                task.stop_reason = terminal.stop_reason
            task.completed_at = time.time()
        return terminal


def _terminal_mark_call(task: BackgroundTask, terminal: TaskTerminal) -> Any:
    """构造 mark_terminal 协程（_persist_run_terminal / _finalize_task_sync 共用）。"""
    from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService

    return SubagentSessionService.mark_terminal(
        run_id=task.run_id,
        status=terminal.run_status,
        content=terminal.content,
        error=terminal.error,
        finish_reason=terminal.finish_reason,
        usage=terminal.usage,
        model_calls=terminal.model_calls,
    )


async def _persist_run_terminal(task: BackgroundTask, terminal: TaskTerminal) -> None:
    """落 run 终态（不发布事件；无 run_id 的 shell/测试任务跳过）。"""
    if not task.run_id:
        return
    from noesis.runtime.main_loop import run_on_main_loop

    terminal_future = run_on_main_loop(
        _terminal_mark_call(task, terminal),
        name=f"subagent-terminal:{task.run_id}",
    )
    if terminal_future is not None:
        await asyncio.wrap_future(terminal_future)


def _claim_terminal_publish(entry: _TaskEntry) -> bool:
    """终态事件归属权：持锁 check-and-set，唯一持有者发布事件。

    必须拿锁：sync 收口（API 线程的 cancel / 沙箱销毁）与 async 收口
    （隔离 loop）跨线程并发，无锁的检查后写入可双发终态事件。
    认领保持在落库之后（发布前）：先行认领再落库的协程若中途被卡死，
    会以已认领状态阻止对账兜底。
    """
    with _TASKS_LOCK:
        if entry.terminal_published:
            return False
        entry.terminal_published = True
        return True


def _publish_terminal_events(task: BackgroundTask, terminal: TaskTerminal) -> None:
    _publish_run_event(
        task, "run.finished", content=terminal.content, finish_reason=terminal.finish_reason,
    )
    _publish_task_event(task, "terminal")
    _notify_terminal(task)


async def _finalize_task(
    entry: _TaskEntry,
    terminal: TaskTerminal,
    *,
    persist_timeout: Optional[float] = None,
) -> bool:
    """唯一终态收口（异步）：状态转移 + run 落库 + 终态事件恰好一次。

    - 已终态重入（先前收口中途崩溃后补跑 / 乐观停止后执行侧到达）不覆盖
      状态，按既有终态语义补落库；事件以 terminal_published 归属权保证不重发
    - persist_timeout：对账路径的有界落库（超时记错误，事件照发）
    """
    task = entry.task
    terminal = _accept_terminal(entry, terminal)
    _disarm_terminal_timers(entry)
    try:
        if persist_timeout is not None:
            await asyncio.wait_for(
                _persist_run_terminal(task, terminal), timeout=persist_timeout,
            )
        else:
            await _persist_run_terminal(task, terminal)
    except Exception:
        # 落库失败不吞事件：通知与 drain 唤醒照发（否则会话队列停摆）
        logger.opt(exception=True).error(
            "bg subagent terminal persist failed task_id={} finish_reason={}",
            task.task_id,
            terminal.finish_reason,
        )
    if _claim_terminal_publish(entry):
        _publish_terminal_events(task, terminal)
    return True


def _finalize_task_sync(entry: _TaskEntry, terminal: TaskTerminal) -> bool:
    """同步终态收口（cancel 即时分支 / shell 超时 / 沙箱销毁）。

    与 _finalize_task 同语义，但落库 fire-and-forget——调用线程不可等待，
    主 loop 异步落库失败只在主 loop 侧日志可见。
    """
    task = entry.task
    terminal = _accept_terminal(entry, terminal)
    _disarm_terminal_timers(entry)
    if task.run_id:
        from noesis.runtime.main_loop import run_on_main_loop

        run_on_main_loop(
            _terminal_mark_call(task, terminal),
            name=f"subagent-terminal:{task.run_id}",
        )
    if _claim_terminal_publish(entry):
        _publish_terminal_events(task, terminal)
    return True


async def _finalize_stop(
    entry: _TaskEntry,
    task: BackgroundTask,
    outcome: Optional[_TurnOutcome],
) -> None:
    """协作停止 / 硬杀的停止收口编排：部分成果回收 + 唯一终态收口。

    - 部分成果以落库投影为权威来源（覆盖全部轮次与硬杀边界前产出），
      无标准 run（测试）退回当前 turn 投影；写入 task.result 供通知预览
    - 硬杀（outcome=None）沿用 run 已积累快照（content=None 语义）
    - 若本协程在收口途中被卡死，_force_terminal 已接管发布，晚到重入
      只补落库不重发事件（归属权在 _finalize_task 内部认领）
    """
    # 入口先拆定时器：宽限/看门狗不得在部分成果回收（跨 loop DB 往返，
    # 大投影可达秒级）期间硬杀收口协程本身——否则收口被打断后只能等
    # 对账兜底，且回收的 content/usage 载荷全部丢失
    _disarm_terminal_timers(entry)
    partial = await _collect_persisted_text(task)
    if not partial and outcome is not None:
        partial = _turn_text_parts(outcome)
    if partial:
        task.result = f"{_PARTIAL_OUTPUT_PREFIX}\n{partial[:_PARTIAL_RESULT_MAX_CHARS]}"
    terminal = replace(
        _stop_terminal(entry),
        content=copy.deepcopy(outcome.content) if outcome is not None else None,
        usage=outcome.usage or None if outcome is not None else None,
        model_calls=outcome.model_calls or None if outcome is not None else None,
    )
    await _finalize_task(entry, terminal)
    logger.info(
        "bg subagent stopped cooperatively task_id={} reason={} steps={} duration={:.1f}s partial={}",
        task.task_id,
        terminal.stop_reason,
        task.step_count,
        (task.completed_at or time.time()) - task.started_at,
        bool(task.result),
    )


async def _force_terminal(entry: _TaskEntry) -> None:
    """硬杀对账的强制终态：协程未按约收口时不依赖其配合直接落终态。

    与 _finalize_stop 的硬杀分支同语义（content=None 沿用已积累快照），
    但不做部分成果回收——被卡死的协程可能正持有投影 builder。落库有界
    等待：对账路径自身不允许无限等待（否则只是把卡死换了个位置）。
    """
    task = entry.task
    if entry.terminal_published:
        return
    logger.error(
        "bg subagent stop reconcile: hard cancel 后协程未收口，强制终态 task_id={}",
        task.task_id,
    )
    await _finalize_task(
        entry, _stop_terminal(entry), persist_timeout=entry.stop_reconcile_seconds,
    )
    logger.info(
        "bg subagent force finalized task_id={} reason={} steps={}",
        task.task_id,
        task.stop_reason or "cancelled",
        task.step_count,
    )
def _arm_stop_grace(entry: _TaskEntry) -> None:
    """停止宽限 watchdog：宽限内静止边界未到达即回退硬杀。

    先摘旧句柄——cancel 与超时协作路径并发触发时不得泄漏定时器。
    """
    _disarm_stop_grace(entry)
    _disarm_watchdog(entry)
    _loop_timer_arm(entry, "stop_grace_handle", entry.stop_grace_seconds, _on_stop_grace_timeout)


def _disarm_stop_grace(entry: _TaskEntry) -> None:
    if entry.stop_grace_handle is not None:
        entry.stop_grace_handle.cancel()
        entry.stop_grace_handle = None


def _disarm_stop_reconcile(entry: _TaskEntry) -> None:
    if entry.stop_reconcile_handle is not None:
        entry.stop_reconcile_handle.cancel()
        entry.stop_reconcile_handle = None


def _on_stop_grace_timeout(entry: _TaskEntry) -> None:
    """停止宽限超时：回退硬杀（CancelledError → _finalize_stop(outcome=None)）。"""
    entry.stop_grace_handle = None
    if not entry.cooperative_stop_signalled:
        return
    logger.warning(
        "bg subagent stop grace exceeded, hard cancel task_id={}",
        entry.task.task_id,
    )
    if entry.future is not None and not entry.future.done():
        # cancel 返回 False 仅发生在 future 已完成——协程已自行收尾
        entry.future.cancel()
    # 终态不依赖被取消协程的配合：CancelledError 可能在深层执行链被吸收
    # （曾出现硬取消后 except CancelledError 分支未执行、run 永久 RUNNING），
    # 对账 watchdog 在 reconcile 窗口后强制落终态
    _disarm_stop_reconcile(entry)
    _loop_timer_arm(
        entry, "stop_reconcile_handle", entry.stop_reconcile_seconds, _on_stop_reconcile_timeout,
    )


def _on_stop_reconcile_timeout(entry: _TaskEntry) -> None:
    """硬杀后对账：协程未按约收口时强制落终态。"""
    entry.stop_reconcile_handle = None
    if entry.terminal_published:
        return
    task = entry.task
    logger.error(
        "bg subagent stop reconcile due task_id={} status={}（CancelledError 未按约传播至收口）",
        task.task_id,
        task.status.value,
    )
    loop = _ensure_loop()
    # 强引用挂在 entry 上：裸 create_task 无引用时可能被 GC 中途回收
    entry.stop_reconcile_task = loop.create_task(_force_terminal(entry))


def _merged_live_usage(entry: "_TaskEntry", bridge: Any) -> dict[str, Any]:
    """实时统计口径：已完成 turn 累计 + 当前 turn bridge 累计（数值相加）。

    bridge 每 turn 重建（message_usage 从零起算），跨轮合并保证与终态
    DB 重建（各 turn extra.usage 累加）一致；turns 按已完成轮次 +1。
    """
    merged = merge_usage(entry.accumulated_usage, bridge.message_usage)
    merged["turns"] = entry.task.turn_count + 1
    return merged


async def _run_turn_via_pipeline(
    entry: _TaskEntry,
    task: BackgroundTask,
    agent: Any,
    source: Any,
) -> _TurnOutcome:
    """单 turn 经统一管道执行：astream_events → RuntimeEventMapper → typed RunEvent。

    与主链路同一条事件映射（usage 累计 / 上下文快照语义同源）；
    本函数只做 executor 侧消费：进度摘要、子会话投影、快照发布与终态汇总。
    """
    session_id = task.child_session_id or task.task_id
    bridge = LangGraphSseBridge(
        session_id,
        assistant_message_id=task.assistant_message_id,
        model_id=task.model_id,
    )
    builder = AssistantMessageBuilder(
        session_id=session_id,
        message_id=task.assistant_message_id or bridge.assistant_message_id,
    )
    ctx = new_stream_ctx()
    mapper = RuntimeEventMapper(bridge)
    outcome = _TurnOutcome()
    last_ai_message: Optional[AIMessage] = None

    def _consume(events: list) -> None:
        for event in events:
            if isinstance(event, WireFrame):
                if event.event == "context-update":
                    snapshot = event.data.get("context")
                    if isinstance(snapshot, dict):
                        _apply_context_snapshot(task, snapshot)
                # 全部 bridge 帧经投递内核转发（与主链路同一帧词汇）：
                # delta / 实时统计为 transient（不占号不进缓存），边界帧
                # durable（占号可重放）；内容权威=落库检查点 + 快照恢复
                _publish_run_event(
                    task, event.event,
                    wire=dict(event.data),
                    transient=event.event in ("text-delta", "reasoning-delta", "stats-update"),
                )
                continue
            if isinstance(event, RunPaused):
                outcome.finish_reason = event.finish_reason or "paused"
                if event.usage:
                    outcome.usage = dict(event.usage)
                if event.model_calls:
                    outcome.model_calls = list(event.model_calls)
            elif isinstance(event, RunCompleted):
                outcome.finish_reason = event.finish_reason
                if event.usage:
                    outcome.usage = dict(event.usage)
                if event.model_calls:
                    outcome.model_calls = list(event.model_calls)
            elif isinstance(event, RunAborted):
                outcome.finish_reason = event.reason
            elif isinstance(event, RunError):
                outcome.error_message = event.message
                outcome.finish_reason = event.finish_reason or "error"

    stream_args = {
        "input": source,
        "config": _config(entry),
        "langfuse_session_id": session_id,
    }
    async for raw in stream_agent_events(
        agent,
        stream_args,
        task_id=task.task_id,
        message_id=task.assistant_message_id or "",
    ):
        _consume(mapper.map_item(raw, builder, ctx))
        raw_event = raw.get("event")
        # 进度摘要与投影边界：模型消息 / 工具结束（与 values 每步 diff 同可见节奏）
        if raw_event == "on_chat_model_end":
            output = (raw.get("data") or {}).get("output")
            if isinstance(output, AIMessage):
                last_ai_message = output
                text = _record_progress_from_model_end(task, output)
                if text.strip():
                    outcome.final_text = text
                # 实时统计（瞬态）：跨轮累计 + 本 turn bridge 累计，口径与
                # 终态 DB 重建一致；子会话详情页据此渲染主 Agent 同款统计行
                _publish_run_event(
                    task, "stats-update",
                    wire=_merged_live_usage(entry, bridge), transient=True,
                )
                # 输出截断一等终止：provider 以 length 截断（含参数被截的工具调用）
                if normalize_provider_finish_reason(
                    (getattr(output, "response_metadata", None) or {}).get("finish_reason")
                ) == "length":
                    outcome.truncated = True
                await _projection_boundary(task, builder)
                _publish_task_event(task, "progress")
                # 协作停止·静止边界：模型消息完整且无未应答工具调用
                if entry.cooperative_stop_signalled and not getattr(output, "tool_calls", None):
                    outcome.cooperative_stop = True
                    break
        elif raw_event == "on_tool_end":
            output = (raw.get("data") or {}).get("output")
            if output is not None:
                text = _record_progress_from_tool_end(task, output)
                if text.strip():
                    outcome.final_text = text
                await _projection_boundary(task, builder)
                _publish_task_event(task, "progress")
                # 协作停止·静止边界：工具结果已落定并投影
                if entry.cooperative_stop_signalled:
                    outcome.cooperative_stop = True
                    break
    if outcome.cooperative_stop:
        # 静止边界退出：投影已在边界发布（含最后一步产出）；usage 取已累计值
        outcome.fallback_error = _final_model_fallback_error(last_ai_message)
        outcome.content = builder.to_dict()
        return outcome
    # 流收尾（stream_agent_events 必产 __tw_finish__，此处为幂等兜底）
    _consume(mapper.finalize())
    outcome.fallback_error = _final_model_fallback_error(last_ai_message)
    outcome.content = builder.to_dict()
    # 最终投影：末段文本在 finish 时才 flush 进 builder，此处发布一次完整内容
    # （与旧 values 模式最后一个 chunk 含最终文本的可见节奏一致）
    if task.run_id and outcome.content.get("parts"):
        await _persist_child_projection(task, outcome.content)
    return outcome


async def _arun(
    entry: _TaskEntry,
    *,
    initial_source: Any = None,
) -> None:
    """执行一轮或多轮 turn。

    - start：initial_source 为原始 description 的 HumanMessage state
    - 冷恢复（send_message 对 completed 任务）：initial_source 为追加消息
    - kind="shell"：分派到 _arun_shell（无 worker / 无 turn 概念）
    turn 正常结束后若 followup 队列非空，链式开下一个 turn（同 thread
    追加 HumanMessage），队列清空前任务保持 running。
    """
    task = entry.task
    if task.status.is_terminal:
        # 调度窗口内已被 cancel（乐观终态已落）：本协程按未启动处理。
        # 停止族终态直接走停止收口（落库/事件/通知立即补齐——任务未
        # 执行、无产出可回收，收口本身很快）；其他终态意味着已有完整
        # 收口路径负责，不重复
        if task.status in _STOP_TERMINALS and not entry.terminal_published:
            await _finalize_stop(entry, task, None)
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
        # 首轮输入：initial_source（start 的 description / 冷恢复的追加消息）
        source = (
            initial_source
            if initial_source is not None
            else {"messages": [HumanMessage(content=task.prompt or task.description)]}
        )
        while True:
            outcome = await _run_turn_via_pipeline(entry, task, agent, source)
            if outcome.error_message is not None:
                # 管道产出的流错误：走既有异常收尾（task FAILED + run ERROR）
                raise _TurnPipelineError(
                    outcome.error_message,
                    usage=outcome.usage or None,
                    model_calls=outcome.model_calls or None,
                )
            if outcome.cooperative_stop:
                # 协作停止在静止边界退出：统一取消收尾（部分成果保留）
                await _finalize_stop(entry, task, outcome)
                return
            if outcome.final_text:
                task.result = outcome.final_text
            if outcome.truncated:
                # 截断一等终止（design D4）：task.result 走部分成果提取并标注截断原因，
                # 通知预览随之反映（「已完成」+ 截断标注而非伪装完整产出）
                partial = _turn_text_parts(outcome)
                task.result = (
                    f"输出截断（finish_reason=length）：\n{partial}"
                    if partial else "输出截断（finish_reason=length）"
                )
            # 实时统计的跨轮累计：本 turn usage 并入
            if outcome.usage:
                entry.accumulated_usage = merge_usage(
                    entry.accumulated_usage, outcome.usage,
                )
            # followup 链：队列非空则同 thread 开下一个 turn
            next_followup = _pop_first_followup(entry)
            if next_followup is None:
                break
            # 恢复 RUNNING 原子化并提前到 await 链之前：停止在 turn 收尾窗口受理时
            # 此处直接取消收尾（不再新开 run）；链内再受理由下一 turn 的静止边界退出
            if not _try_transition(task, BgTaskStatus.RUNNING):
                await _finalize_stop(entry, task, outcome)
                return
            next_message, next_user_message_id, next_params = next_followup
            # 该 turn 指定了新模型/新档位 → 失效已编译 worker，下一轮以新参数续跑同 thread
            # （档位 ContextVar 在 _ensure_agent 编译前统一设置）
            if _apply_turn_params(entry, next_params):
                agent = await _ensure_agent(entry)
            turn_fallback_error = outcome.fallback_error
            turn_status = _turn_run_status(outcome, turn_fallback_error)
            turn_reason = _turn_finish_reason(outcome, turn_fallback_error)
            if task.run_id:
                from noesis.services.subagent_runtime_port import SubagentSessionPort as SubagentSessionService
                from noesis.runtime.main_loop import run_on_main_loop

                current_run_future = run_on_main_loop(
                    SubagentSessionService.mark_terminal(
                        run_id=task.run_id,
                        status=turn_status,
                        content=copy.deepcopy(outcome.content),
                        error=turn_fallback_error,
                        finish_reason=turn_reason,
                        usage=outcome.usage or None,
                        model_calls=outcome.model_calls or None,
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
                    entry.turn_seed_content = None
            task.completed_at = None
            logger.info(
                "bg subagent followup turn task_id={} queued={}",
                task.task_id,
                len(entry.followups),
            )
            source = {"messages": [HumanMessage(content=next_message)]}
        final_fallback_error = outcome.fallback_error
        # 终态收口（先到获胜）：若停止已抢先受理（乐观 CANCELLED），
        # _accept_terminal 把本规格降级为停止语义并保留载荷
        await _finalize_task(
            entry,
            TaskTerminal(
                task_status=(
                    BgTaskStatus.FAILED if final_fallback_error else BgTaskStatus.COMPLETED
                ),
                run_status=_turn_run_status(outcome, final_fallback_error),
                finish_reason=_turn_finish_reason(outcome, final_fallback_error),
                error=final_fallback_error,
                content=copy.deepcopy(outcome.content),
                usage=outcome.usage or None,
                model_calls=outcome.model_calls or None,
            ),
        )
        logger.info(
            "bg subagent completed task_id={} steps={} duration={:.1f}s",
            task.task_id,
            task.step_count,
            task.completed_at - task.started_at,
        )
    except asyncio.CancelledError:
        # 硬杀兜底（停止宽限超时 / 沙箱销毁连坐）：完整终态收尾。
        # 投影沿用最后一次边界 persist（mark_terminal content=None 语义）；
        # 部分成果从落库投影回收（覆盖边界前产出；无 DB 降级为空）。
        # 乐观终态下 status 在受理时已落 CANCELLED/TIMED_OUT——守卫不得查
        # is_terminal（恒 False 导致本分支死亡），以 terminal_published（收口
        # 是否已发布）为准；停止信号已被冷恢复清除的旧协程消亡于此（不收口，
        # 复活轮的生命周期归新协程）
        if not entry.terminal_published and (
            entry.cooperative_stop_signalled or not task.status.is_terminal
        ):
            await _finalize_stop(entry, task, None)
    except Exception as exc:
        # 收口已完整发布（状态+落库+事件）：迟到异常只记录，不覆盖终态、不重发
        if entry.terminal_published:
            logger.opt(exception=True).error(
                "bg subagent exception after terminal finalized task_id={}",
                task.task_id,
            )
            return
        # 停止受理中（乐观终态已落）：异常协程正在消亡，不会再有静止
        # 边界，立即走停止收口（保留部分成果回收），不再判 FAILED；
        # 普通异常走 FAILED（若终态已是停止族，_accept_terminal 自动
        # 降级保留载荷）
        if entry.cooperative_stop_signalled:
            await _finalize_stop(entry, task, None)
        else:
            await _finalize_task(
                entry,
                TaskTerminal(
                    task_status=BgTaskStatus.FAILED,
                    run_status=RunStatus.ERROR,
                    finish_reason="error",
                    error=str(exc),
                    usage=getattr(exc, "usage", None),
                    model_calls=getattr(exc, "model_calls", None),
                ),
            )
        logger.opt(exception=True).error(
            "bg subagent failed task_id={}",
            task.task_id,
        )


async def _arun_followup(
    entry: _TaskEntry,
    text: str,
    user_message_id: Optional[str] = None,
    params: Optional[_TurnParams] = None,
) -> None:
    """completed child session 的新 turn：先建标准 run，再进入 worker。

    params 携带该 turn 的模型/推理档位覆盖；变化时以新参数编译 worker
    （同 thread 续跑）。新 turn 的投影由独立 builder 从零累积（统一管道）。

    前置段（worker 编译 / run 创建）失败必须显式收口 FAILED：send_message
    对本协程 fire-and-forget，异常会滞留在未观察的 concurrent Future 里被
    静默吞掉——任务卡 RUNNING、后续追问进队列无人消费（冷恢复静默失败
    事故：跨 loop 连接错误曾走此路径无任何日志）。
    """
    task = entry.task
    try:
        _apply_turn_params(entry, params)
        # 预编译 worker（参数变化时失效重编）；_arun 会复用缓存结果
        await _ensure_agent(entry)
        task.turn_count += 1
        if entry.followup_factory is not None:
            launch = entry.followup_factory(
                task.child_session_id or task.task_id,
                text,
                user_message_id,
            )
            if inspect.isawaitable(launch):
                launch = await launch
            task.run_id = str(launch.get("run_id") or "") or None
            task.assistant_message_id = str(launch.get("assistant_message_id") or "") or None
            task.projection_sequence = 0
            entry.turn_seed_content = None
    except Exception as exc:
        await _finalize_followup_prelude_failure(entry, task, exc)
        return
    await _arun(entry, initial_source={"messages": [HumanMessage(content=text)]})


async def _finalize_followup_prelude_failure(
    entry: _TaskEntry,
    task: BackgroundTask,
    exc: BaseException,
) -> None:
    """冷恢复前置段失败收口：task FAILED + run ERROR + 终态事件与通知。

    run 未创建时（factory 抛出）task.run_id 仍指向上一个已完成 run，
    mark_terminal 的 compare-and-set 会安全跳过。停止抢先受理时
    _accept_terminal 把 FAILED 降级为停止语义并保留异常载荷。
    """
    await _finalize_task(
        entry,
        TaskTerminal(
            task_status=BgTaskStatus.FAILED,
            run_status=RunStatus.ERROR,
            finish_reason="error",
            error=str(exc),
        ),
    )
    logger.opt(exception=True).error(
        "bg subagent followup prelude failed task_id={}",
        task.task_id,
    )


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
            task.command or "",
            **({"timeout": timeout} if timeout is not None else {}),
        )
        task.result = _format_shell_result(response)
        _finalize_task_sync(
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
            _finalize_task_sync(entry, _stop_terminal(entry))
    except Exception as exc:
        _finalize_task_sync(
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
        if entry.future is not None and not entry.future.done():
            entry.future.cancel()
        _finalize_task_sync(
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


def _arm_watchdog(entry: _TaskEntry) -> None:
    _disarm_watchdog(entry)
    _loop_timer_arm(entry, "watchdog_handle", entry.timeout_seconds, _on_task_timeout)


def _schedule_entry_locked(entry: _TaskEntry) -> None:
    """把已获槽位的任务调度到执行 loop（须持 _TASKS_LOCK）。

    future 创建与 watchdog 装载必须在锁内完成：若状态置 RUNNING 后、
    future 尚未创建前被 cancel，cancel 拿不到 future 无法真正停止协程，
    任务会跑完并以 COMPLETED 覆盖 CANCELLED。run_coroutine_threadsafe /
    call_later 均为非阻塞提交，锁内调用安全；SSE 事件发布留待锁外。
    """
    loop = _ensure_loop()
    entry.future = _submit_isolated(loop, behavior_of(entry.task.kind).run(entry))
    if entry.timeout_seconds > 0:
        _arm_watchdog(entry)


def _publish_entry_started(entry: _TaskEntry) -> None:
    _publish_task_event(entry.task, "started")
    _publish_run_event(entry.task, "run.started")


_SUBMIT_SEQ = 0


def _next_submit_seq() -> int:
    global _SUBMIT_SEQ
    _SUBMIT_SEQ += 1
    return _SUBMIT_SEQ


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
    """任务落终态后唤醒排队任务：两级准入（先全局后会话），全局按提交序。

    跨会话队列按 submit_seq 全局排序唤醒（防单会话占满总闸饿死其他
    会话）；会话上限满的候选跳过留队，不阻塞其他会话。在 _notify_terminal
    统一触发，覆盖完成、失败、超时、取消、沙箱销毁全部终态路径。
    """
    global _PENDING_QUEUES
    while True:
        with _TASKS_LOCK:
            # 各会话队首候选（会话内 FIFO），按提交序全局排序
            candidates: list[_TaskEntry] = []
            for queue in _PENDING_QUEUES.values():
                while queue and queue[0].task.status != BgTaskStatus.QUEUED:
                    queue.pop(0)
                if queue:
                    candidates.append(queue[0])
            if not candidates:
                _PENDING_QUEUES.clear()
                return
            candidates.sort(key=lambda e: e.submit_seq)
            global_active = sum(
                1
                for e in _TASKS.values()
                if e.task.status in _SLOT_STATUSES
            )
            entry: Optional[_TaskEntry] = None
            for candidate in candidates:
                if candidate.max_global > 0 and global_active >= candidate.max_global:
                    continue  # 总闸满：本候选留队，看其他会话（亦满则全部留队）
                session_active = sum(
                    1
                    for e in _TASKS.values()
                    if e.task.session_id == candidate.task.session_id
                    and e.task.status in _SLOT_STATUSES
                )
                if session_active >= candidate.session_max_concurrent:
                    continue  # 会话满：留队不阻塞其他会话
                entry = candidate
                break
            if entry is None:
                return
            for queue in _PENDING_QUEUES.values():
                if queue and queue[0] is entry:
                    queue.pop(0)
                    break
            _PENDING_QUEUES = {
                k: v for k, v in _PENDING_QUEUES.items() if v
            }
            entry.task.status = BgTaskStatus.RUNNING
            # 锁内调度（同 _launch：防 RUNNING 后 future 未建即被 cancel 的竞态）
            _schedule_entry_locked(entry)
        _publish_entry_started(entry)
        logger.info(
            "bg task dequeued task_id={} session_id={}",
            entry.task.task_id, entry.task.session_id,
        )


def _disarm_watchdog(entry: _TaskEntry) -> None:
    if entry.watchdog_handle is not None:
        entry.watchdog_handle.cancel()
        entry.watchdog_handle = None


def _on_task_timeout(entry: _TaskEntry) -> None:
    """任务总时限：乐观终态（TIMED_OUT）+ 协作停止信号 + 宽限 watchdog。

    受理即落 TIMED_OUT（UI 同步可见）；执行侧经静止边界协作退出（部分
    成果保留），宽限超时由硬杀兜底。置位持 _TASKS_LOCK：与 cancel() 的
    停止受理互斥，先到者保留 stop_reason（用户先取消则报 cancelled，
    先超时则报 timed_out——后写者胜会误报）。
    """
    # 锁内只做状态判定与置位；shell 硬杀/事件发布在锁外——
    # _on_timeout_hard 的通知与 drain 需要再拿 _TASKS_LOCK，持锁调用即死锁
    shell_hard = False
    with _TASKS_LOCK:
        task = entry.task
        if task.status.is_terminal:
            return
        if behavior_of(task.kind).on_timeout_locked(entry):
            shell_hard = True
        else:
            task.status = BgTaskStatus.TIMED_OUT
            task.stop_reason = "timed_out"
            task.completed_at = time.time()
            entry.cooperative_stop_signalled = True
    if shell_hard:
        _on_timeout_hard(entry)
        return
    _arm_stop_grace(entry)


def _on_timeout_hard(entry: _TaskEntry) -> None:
    """即时超时终态（shell 专用）：硬杀 + TIMED_OUT + 通知。"""
    _disarm_watchdog(entry)
    if entry.future is not None and not entry.future.done():
        entry.future.cancel()
    _finalize_task_sync(
        entry,
        TaskTerminal(
            task_status=BgTaskStatus.TIMED_OUT,
            run_status=RunStatus.ERROR,
            finish_reason="timeout",
            error=f"后台任务超时（{int(entry.timeout_seconds)}s）",
            stop_reason="timed_out",
        ),
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


class _SubagentKind:
    """子 Agent 委派：可追问、有轮次概念、协作停止、协作超时。"""

    kind = "subagent"
    supports_followup = True
    has_turns = True

    @staticmethod
    def reject_followup_text() -> str:
        raise AssertionError("subagent supports followup")  # pragma: no cover

    @staticmethod
    def run(entry: "_TaskEntry") -> Any:
        return _arun(entry)

    @staticmethod
    def request_stop(entry: "_TaskEntry") -> "StopMode":
        return StopMode.COOPERATIVE

    @staticmethod
    def on_timeout_locked(entry: "_TaskEntry") -> bool:
        return False  # 协作 timed_out：静止边界退出 + 宽限兜底


class _ShellKind:
    """后台命令：不可追问、无轮次、立即取消、硬杀超时。"""

    kind = "shell"
    supports_followup = False
    has_turns = False

    @staticmethod
    def reject_followup_text() -> str:
        return "该任务为后台命令任务，不支持追加消息（可用 check_task 收取输出、重新执行请新建命令）"

    @staticmethod
    def run(entry: "_TaskEntry") -> Any:
        return _arun_shell(entry)

    @staticmethod
    def request_stop(entry: "_TaskEntry") -> "StopMode":
        return StopMode.IMMEDIATE_CANCEL

    @staticmethod
    def on_timeout_locked(entry: "_TaskEntry") -> bool:
        return True  # 硬杀：命令在 backend 不可中断


# kind → 行为对象注册表（新增任务类型在此注册，运行时零 kind 判断）
KIND_BEHAVIORS: dict[str, Any] = {
    "subagent": _SubagentKind(),
    "shell": _ShellKind(),
}


class _ExecutorRuntimePort:
    # 单一异步 followup 入口（校验折叠在锁内前置；同步/异步双版本的
    # 端口漂移事故见 subagent_runtime_port 同名注释）
    deliver_followup = staticmethod(BackgroundTaskExecutor.deliver_followup)
    cancel = staticmethod(BackgroundTaskExecutor.cancel)
    subscribe_run_events = staticmethod(subscribe_run_events)
    unsubscribe_run_events = staticmethod(unsubscribe_run_events)
    get_run_event_history = staticmethod(get_run_event_history)


configure_executor_port(_ExecutorRuntimePort)


__all__ = [
    "BackgroundTaskExecutor",
    "BackgroundTask",
    "BgTaskStatus",
    "fail_session_shell_tasks",
    "shutdown",
    "shutdown_loop",
]
