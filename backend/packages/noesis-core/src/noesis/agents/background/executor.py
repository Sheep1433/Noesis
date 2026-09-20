"""进程内后台任务门面（BackgroundTaskExecutor）与装配。

机制在 jobs/（state/loop/registry/events/settle），两类执行内核分别在
subagent/kernel.py 与 shell/kernel.py，kind 行为注册表在 kinds.py；本模块
保留 Executor 门面（任务台 CRUD + shutdown 编排）与运行时端口装配。
"""

from __future__ import annotations

import inspect
import time
import uuid
from concurrent.futures import Future
from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage
from noesis.llm.reasoning import get_request_reasoning_effort
from noesis.runtime.logging import logger

from noesis.agents.background.subagent.kernel import (
    _TurnParams,
    _apply_turn_params,
    _arun,
    _arun_followup,
    settle_followup_prelude_failure,
)
from noesis.agents.background.jobs.events import (
    get_run_event_history,
    subscribe_run_events,
    unsubscribe_run_events,
    _publish_task_event,
)
from noesis.agents.background.jobs.loop import (
    _ensure_loop,
    _submit_isolated,
    shutdown_loop,
)
from noesis.agents.background.jobs.registry import (
    _PENDING_QUEUES,
    _TASKS,
    _TASKS_LOCK,
    _TASK_NOT_FOUND,
    _TaskEntry,
    _dequeue_locked,
    _find_entry_locked,
    _next_submit_seq,
    _publish_entry_started,
    _schedule_entry_locked,
)
from noesis.agents.background.jobs.settle import (
    REVIVABLE_END_STATES,
    _stop_terminal,
    cancel_terminal_timers,
    settle_task_sync,
    start_stop_grace_timer,
    start_watchdog_timer,
    cancel_watchdog_timer,
)
from noesis.agents.background.jobs.state import (
    MAX_CONCURRENT_PER_SESSION,
    MAX_FOLLOWUPS,
    STOP_GRACE_SECONDS,
    STOP_RECONCILE_SECONDS,
    SHELL_TASK_TIMEOUT_SECONDS,
    TASK_TIMEOUT_SECONDS,
    _SHELL_DEFAULT_COMMAND_TIMEOUT,
    _SLOT_STATUSES,
    BackgroundTask,
    BgTaskStatus,
)
from noesis.agents.background.kinds import StopMode, behavior_of
from noesis.agents.background.ports import configure_executor_port


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
        """任务级去重来源清单（跨边界传递用；check_async_task / 通知携带）。"""
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
            return list(entry.task.retrieval_sources.values()) if entry else []

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
            if task.status not in REVIVABLE_END_STATES:
                if task.status.is_terminal:
                    raise ValueError(f"任务已结束（{task.status.value}），无法追加消息")
                with entry.followup_lock:
                    if len(entry.followups) >= MAX_FOLLOWUPS:
                        raise ValueError(
                            f"补话队列已满（{MAX_FOLLOWUPS} 条）：请等待当前轮完成后再发，"
                            f"或将多条指示合并为一条"
                        )
                    entry.followups.append(text)
                    entry.followup_message_ids.append(user_message_id)
                    entry.followup_turn_params.append(params)
                _publish_task_event(task, "followup")
                return task.to_dict()
            # 先占位 RUNNING：run 创建窗口内受理的停止由宽限对账兜底
            task.status = BgTaskStatus.RUNNING
            task.result = None
            task.completed_at = None
            # 复活中和：清停止信号、取消旧协程与在飞对账
            # task、重置 terminal_published（复活轮发自己的终态事件与通知）
            entry.cooperative_stop_signalled = False
            if entry.future is not None and not entry.future.done():
                entry.future.cancel()
            entry.future = None
            if entry.stop_reconcile_task is not None and not entry.stop_reconcile_task.done():
                entry.stop_reconcile_task.cancel()
            entry.stop_reconcile_task = None
            entry.terminal_published = False
            cancel_terminal_timers(entry)
        if entry.followup_factory is None:
            loop = _ensure_loop()
            entry.future = _submit_isolated(
                loop, _arun_followup(entry, text, user_message_id, params),
            )
            start_watchdog_timer(entry)
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
            await settle_followup_prelude_failure(entry, task, exc)
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
            start_watchdog_timer(entry)
        _publish_task_event(task, "followup")
        return task.to_dict()

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
                start_stop_grace_timer(entry)
                snapshot = task.to_dict(include_progress=False)
            else:
                # queued（无执行 future）/ IMMEDIATE_CANCEL（命令在 backend
                # 不可中断，无协作边界）：即时终态
                cancel_watchdog_timer(entry)
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
            settle_task_sync(entry, _stop_terminal(entry))
        # RUNNING 协作停止：终态事件与通知由执行协程的静止边界收口发布
        # （携带完整 outcome 的部分成果回收）；宽限超时经硬杀的 CancelledError
        # 路径收口（outcome=None，进度摘要回收），对账 watchdog 兜底不依赖
        # 协程配合——cancel 不自起收口协程，避免与执行侧收口竞争 outcome
        return snapshot

    # -- 内部委托模块实现（见下方模块函数） ----------------------------


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
    # 单一异步 followup 入口（校验折叠在锁内前置；同步/异步双版本的
    # 端口漂移事故见 ports.py 同名注释）
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
    "shutdown",
]
