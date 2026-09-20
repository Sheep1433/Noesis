"""任务登记表：内存注册表、会话排队、并发槽（访问须持 _TASKS_LOCK）。

内存为易失层，进程重启即丢（接受的设计限制，启动对账收口遗留 run）。
"""
from __future__ import annotations

import asyncio
import collections
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from noesis.agents.background.kinds import behavior_of
from noesis.runtime.logging import logger

from noesis.agents.background.jobs.events import _publish_run_event, _publish_task_event
from noesis.agents.background.jobs.loop import _ensure_loop, _submit_isolated
from noesis.agents.background.jobs.state import (
    MAX_PENDING_MESSAGES,
    STOP_GRACE_SECONDS,
    STOP_RECONCILE_SECONDS,
    TERMINAL_RECLAIM_MAX,
    TERMINAL_RETENTION_SECONDS,
    _SLOT_STATUSES,
    BgTaskStatus,
    BackgroundTask,
)

if TYPE_CHECKING:
    from noesis.agents.background.subagent.kernel import _TurnParams


# ---------------------------------------------------------------------------
# 注册表与执行器
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PendingMessage:
    """一条待执行的追加消息（DB pending 行的执行镜像）。

    message_id = child session 的 pending user message 行 id；params 为
    逐 turn 覆盖（模型 / 推理档位；None = 沿用当前）。
    """

    message_id: Optional[str]
    text: str
    params: Optional[Any] = None


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
    # 追加消息队列（执行镜像）：DB pending user message 行是队列事实，
    # 本 deque 只是热集内的执行镜像（出队开 turn；条目回收后由冷恢复从
    # DB 重载）。deliver_message 入队，当前 turn 结束后链式开新 turn
    pending_messages: "collections.deque[_PendingMessage]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_PENDING_MESSAGES),
    )
    # 生效中的模型覆盖：非 None 时 _ensure_agent 以该模型重新编译 worker
    model_override: Optional[str] = None
    # 生效中的推理档位（turn 级；LLM 构造时经 ContextVar 固化为请求参数）。
    # 创建时在父 run 上下文捕获（后台 worker 隔离 loop 干净上下文拿不到父档位）
    turn_reasoning_effort: Optional[str] = None
    # 保护 pending_messages 的跨线程读写（原 followup_lock 更名随队列语义）
    pending_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
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
    # 首个置位者负责发布，settle_stop 与 settle_orphaned_task 竞争时只发一次
    terminal_published: bool = False
    # 回收资格三件套：终态落库成功（耗尽置 exhausted）/ 通知已记录。
    # 回收 = 终态事件已发布 + (落库成功或耗尽) + 通知已记录
    terminal_persist_ok: bool = False
    terminal_persist_exhausted: bool = False
    terminal_notified: bool = False
    # 冷恢复/复活前的终态快照：投递失败回退时恢复（result/error/completed_at/
    # stop_reason/status），避免悬空的回收资格判定
    prev_terminal_snapshot: Optional[dict] = None
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


# 任务不存在时的统一提示：模型惯用短 id，指路 list_async_tasks 避免盲试
_TASK_NOT_FOUND = "后台任务不存在: {task_id}（可用 list_async_tasks 查看完整 task_id）"


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
def _config(entry: _TaskEntry) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": entry.task.child_session_id or entry.task.task_id},
        "recursion_limit": entry.recursion_limit,
    }

def _schedule_entry_locked(entry: _TaskEntry) -> None:
    """把已获槽位的任务调度到执行 loop（须持 _TASKS_LOCK）。

    future 创建与 watchdog 装载必须在锁内完成：若状态置 RUNNING 后、
    future 尚未创建前被 cancel，cancel 拿不到 future 无法真正停止协程，
    任务会跑完并以 COMPLETED 覆盖 CANCELLED。run_coroutine_threadsafe /
    call_later 均为非阻塞提交，锁内调用安全；SSE 事件发布留待锁外。
    """
    from noesis.agents.background.jobs.settle import start_watchdog_timer

    loop = _ensure_loop()
    entry.future = _submit_isolated(loop, behavior_of(entry.task.kind).run(entry))
    if entry.timeout_seconds > 0:
        start_watchdog_timer(entry)


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

def _drain_all_sessions() -> None:
    """对账重建后的全局唤醒：对存在排队候选的会话逐一触发 drain。

    _drain_session_queue 每次调用都做跨会话全局排序（按提交序/created_at），
    重复调用幂等；空队列调用无副作用。
    """
    with _TASKS_LOCK:
        session_ids = list(_PENDING_QUEUES.keys())
    for session_id in session_ids:
        _drain_session_queue(session_id)


def _drain_session_queue(session_id: str) -> None:
    """任务落终态后唤醒排队任务：两级准入（先全局后会话），全局按提交序。

    跨会话队列按 submit_seq 全局排序唤醒（防单会话占满总闸饿死其他
    会话）；会话上限满的候选跳过留队，不阻塞其他会话。在 _notify_terminal
    统一触发，覆盖完成、失败、超时、取消、沙箱销毁全部终态路径。
    """
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
            for _empty_key in [k for k, v in _PENDING_QUEUES.items() if not v]:
                _PENDING_QUEUES.pop(_empty_key, None)
            entry.task.status = BgTaskStatus.RUNNING
            # 锁内调度（同 _launch：防 RUNNING 后 future 未建即被 cancel 的竞态）
            _schedule_entry_locked(entry)
        _publish_entry_started(entry)
        logger.info(
            "bg task dequeued task_id={} session_id={}",
            entry.task.task_id, entry.task.session_id,
        )


# ---------------------------------------------------------------------------
# 终态条目回收：retention + 上限，惰性（通知路径触发）+ 周期兜底。
# 旋钮由 executor 装配时写入（最后构造的实例生效，与 _EXECUTOR 端口同模式）
# ---------------------------------------------------------------------------

_TERMINAL_RETENTION_SECONDS: float = TERMINAL_RETENTION_SECONDS
_TERMINAL_RECLAIM_MAX: int = TERMINAL_RECLAIM_MAX
_RECLAIM_TIMER: Optional[threading.Timer] = None
_RECLAIM_TIMER_LOCK = threading.Lock()
_RECLAIM_INTERVAL_SECONDS = 60.0


def configure_terminal_reclaim(retention_seconds: float, reclaim_max: int) -> None:
    """装配终态回收旋钮（executor __init__ 调用）。"""
    global _TERMINAL_RETENTION_SECONDS, _TERMINAL_RECLAIM_MAX
    _TERMINAL_RETENTION_SECONDS = max(1.0, float(retention_seconds))
    _TERMINAL_RECLAIM_MAX = max(1, int(reclaim_max))


def reclaim_terminal_entries() -> int:
    """回收终态条目：retention 到期 + 上限超出，从最旧起。

    回收资格 = 终态事件已发布（terminal_published）+ 终态落库成功或重试
    耗尽 + 通知已记录。收尾在途的条目 SHALL NOT 被移除；上限超出的部分
    只能通过回收合格条目消化（不合格条目留待收尾完成后由下次触发回收）。
    返回本次回收条数。
    """
    now = time.time()
    evicted = 0
    with _TASKS_LOCK:
        terminal = [
            e for e in _TASKS.values()
            if e.task.status.is_terminal and e.task.completed_at is not None
        ]
        eligible = [
            e for e in terminal
            if e.terminal_published
            and (e.terminal_persist_ok or e.terminal_persist_exhausted)
            and e.terminal_notified
        ]
        # retention 到期全收
        evict_ids = {
            id(e) for e in eligible
            if now - e.task.completed_at >= _TERMINAL_RETENTION_SECONDS
        }
        # 上限超出：剩余合格条目按终态时间从最旧起补收
        remaining_terminal = len(terminal) - len(evict_ids)
        if remaining_terminal > _TERMINAL_RECLAIM_MAX:
            by_oldest = sorted(
                (e for e in eligible if id(e) not in evict_ids),
                key=lambda e: e.task.completed_at or 0.0,
            )
            for e in by_oldest:
                if remaining_terminal <= _TERMINAL_RECLAIM_MAX:
                    break
                evict_ids.add(id(e))
                remaining_terminal -= 1
        if evict_ids:
            for task_id in [
                tid for tid, e in _TASKS.items() if id(e) in evict_ids
            ]:
                _TASKS.pop(task_id, None)
                evicted += 1
    if evicted:
        logger.info("bg task terminal entries reclaimed count={}", evicted)
    return evicted


def _reclaim_loop() -> None:
    global _RECLAIM_TIMER
    try:
        reclaim_terminal_entries()
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).error("bg task terminal reclaim sweep failed")
    with _RECLAIM_TIMER_LOCK:
        if _TASKS:
            _RECLAIM_TIMER = threading.Timer(
                _RECLAIM_INTERVAL_SECONDS, _reclaim_loop,
            )
            _RECLAIM_TIMER.daemon = True
            _RECLAIM_TIMER.start()
        else:
            _RECLAIM_TIMER = None


def ensure_reclaim_timer() -> None:
    """惰性启动周期回收（首个任务入注册表时触发；注册表空转即自停）。"""
    global _RECLAIM_TIMER
    with _RECLAIM_TIMER_LOCK:
        if _RECLAIM_TIMER is not None:
            return
        _RECLAIM_TIMER = threading.Timer(_RECLAIM_INTERVAL_SECONDS, _reclaim_loop)
        _RECLAIM_TIMER.daemon = True
        _RECLAIM_TIMER.start()
