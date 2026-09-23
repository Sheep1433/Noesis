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
    _arun_appended_turn,
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
    _PendingMessage,
    _TaskEntry,
    _dequeue_locked,
    _find_entry_locked,
    _next_submit_seq,
    _publish_entry_started,
    _schedule_entry_locked,
    configure_terminal_reclaim,
    ensure_reclaim_timer,
)
from noesis.agents.background.jobs.settle import (
    REVIVABLE_END_STATES,
    _stop_terminal,
    cancel_terminal_timers,
    settle_delivery_failure,
    settle_task_sync,
    start_stop_grace_timer,
    start_watchdog_timer,
    cancel_watchdog_timer,
)
from noesis.agents.background.jobs.state import (
    MAX_CONCURRENT_PER_SESSION,
    STOP_GRACE_SECONDS,
    STOP_RECONCILE_SECONDS,
    SHELL_TASK_TIMEOUT_SECONDS,
    TASK_TIMEOUT_SECONDS,
    TERMINAL_RECLAIM_MAX,
    TERMINAL_RETENTION_SECONDS,
    _SHELL_DEFAULT_COMMAND_TIMEOUT,
    _SLOT_STATUSES,
    BackgroundTask,
    BgTaskStatus,
)
from noesis.agents.background.kinds import StopMode, behavior_of
from noesis.agents.background.ports import (
    SessionOpsPort,
    ShellJobPort,
    SubagentSessionPort,
    configure_executor_port,
)


class BackgroundTaskExecutor:
    """start/check/cancel/list 的进程内执行面。"""

    # 最近装配的实例（单执行 leader 进程内语义上单例）：对账重建等
    # 无实例上下文的入口经 default() 取配置与冷恢复解析器
    _active: Optional["BackgroundTaskExecutor"] = None

    @classmethod
    def default(cls) -> "BackgroundTaskExecutor":
        if cls._active is None:
            raise RuntimeError("BackgroundTaskExecutor 未装配")
        return cls._active

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
        terminal_retention_seconds: float = TERMINAL_RETENTION_SECONDS,
        terminal_reclaim_max: int = TERMINAL_RECLAIM_MAX,
        # 冷恢复配方解析： (subagent_type, model_id) → worker_factory | None。
        # 由装配方（super_agent）注入角色注册表解析；None = 冷恢复不可用
        cold_resolver: Optional[Callable[[Optional[str], Optional[str]], Optional[Callable[..., Any]]]] = None,
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
        # 终态条目热集回收旋钮（回收后查询走 DB 投影兜底）
        self._terminal_retention = max(1.0, terminal_retention_seconds)
        self._terminal_reclaim_max = max(1, terminal_reclaim_max)
        self._cold_resolver = cold_resolver
        configure_terminal_reclaim(self._terminal_retention, self._terminal_reclaim_max)
        BackgroundTaskExecutor._active = self

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

    # -- 查询 DB 兜底（热集 miss：终态已回收 / 跨进程） ------------------

    async def check_with_fallback(self, task_id: str) -> Optional[dict[str, Any]]:
        """热集内读内存快照，miss 读 DB 投影（同源状态映射函数）。

        返回 dict 附带 retrieval_sources / undelivered_messages；None =
        无任何 DB 事实（真不存在）。
        """
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
        if entry is not None:
            snap = entry.task.to_dict(include_progress=False)
            snap["retrieval_sources"] = list(entry.task.retrieval_sources.values())
            snap["undelivered_messages"] = 0
            return snap
        # 两 kind 兜底：subagent 走 child session/run 投影，shell 走事实行
        projection = await SubagentSessionPort.db_task_projection(task_id)
        if projection is not None:
            return projection
        return await ShellJobPort.get_task(task_id)

    async def list_with_fallback(self, session_id: str) -> list[dict[str, Any]]:
        memory = self.list_for_session(session_id)
        seen = {str(t.get("task_id")) for t in memory} | {
            str(t.get("child_session_id")) for t in memory if t.get("child_session_id")
        }
        rows = await SubagentSessionPort.list_db_task_projections(session_id)
        merged = memory + [r for r in rows if str(r.get("task_id")) not in seen]
        shell_rows = await ShellJobPort.list_for_session(session_id)
        merged += [
            r for r in shell_rows if str(r.get("task_id")) not in seen
        ]
        return sorted(merged, key=lambda t: t.get("started_at") or 0)

    async def cancel_with_fallback(self, task_id: str) -> dict[str, Any]:
        """取消：热集受理；miss 回退 DB——终态任务幂等返回快照（不误报不存在）。"""
        try:
            return self.cancel(task_id)
        except ValueError:
            projection = await SubagentSessionPort.db_task_projection(task_id)
            if projection is None:
                projection = await ShellJobPort.get_task(task_id)
            if projection is not None and BgTaskStatus(
                projection.get("status")
            ).is_terminal:
                return projection
            raise

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
        turn_factory: Optional[Callable[[str, str, Optional[str]], Any]] = None,
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
            turn_factory=turn_factory,
            recursion_limit=self._recursion_limit,
            timeout_seconds=self._task_timeout,
            # 创建时档位继承：start 在父 run 上下文调用（ContextVar 可见）；
            # worker 在隔离 loop 编译前经 _arun 显式设置回该档位
            turn_reasoning_effort=get_request_reasoning_effort(),
        )
        self._launch(entry)
        if child_session_id:
            # task_id 落 child session extra（bg_task_id）：DB 投影据此解析
            # 已回收任务（内存 bg-* id 不入任何其他表）。fire-and-forget，
            # 但失败必须留痕——此值缺失会让回收后的 bg-* 查询静默退化为
            # 「任务不存在」
            from noesis.runtime.main_loop import run_on_main_loop

            async def _persist_task_id() -> None:
                try:
                    from noesis.storage.postgres.manager import pg_manager

                    async with pg_manager.get_async_session_context() as db:
                        await SessionOpsPort.merge_session_extra(
                            child_session_id, user_id, {"bg_task_id": task_id}, db=db,
                        )
                except Exception:  # noqa: BLE001
                    logger.opt(exception=True).error(
                        "bg task id persist failed task_id={} child={}",
                        task_id, child_session_id,
                    )

            run_on_main_loop(
                _persist_task_id(), name=f"bg-task-id-persist:{task_id}",
            )
        ensure_reclaim_timer()
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
        # shell 事实行落库（task_id 即主键）：查询兜底与重启对账的事实源
        from noesis.runtime.main_loop import run_on_main_loop

        run_on_main_loop(
            ShellJobPort.persist_start(
                task_id=task_id,
                session_id=session_id,
                user_id=user_id,
                command=task.command or "",
                status=task.status.value,
            ),
            name=f"bg-shell-row:{task_id}",
        )
        ensure_reclaim_timer()
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



    async def deliver_message(
        self,
        task_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> dict[str, Any]:
        """追加消息消费路径（leader 命令消费者调用）：入执行队列 / 冷恢复。

        - 受理（写 pending 行 + 插命令、容量执法）在 `send_message` 的
          accept 端（任意实例）；本方法只消费已受理的消息——
          `user_message_id` 必须来自受理写入的 pending 行
        - 内存 miss 走冷恢复：从 DB 投影 + descriptor 重建执行条目（仅
          可续终态 completed / cancelled）并重载 pending 行
        - 镜像查重 + 行状态保证命令重放/租约重置后不重复消费
        - 投递失败不终态化任务：settle_delivery_failure 裁决回退（冷恢复
          窗口内受理的停止终态获胜）
        """
        text = message.strip()
        if not text:
            raise ValueError("消息不能为空")
        if user_message_id is None:
            # 消费路径只消费已受理（accept 已落 pending 行）的消息：
            # 行的写入与容量执法在受理端（任意实例），此处仅 leader 消费
            raise ValueError("追加消息消费缺少 message_id（受理端命令载荷缺失）")
        params = _TurnParams(model_id=model_id, reasoning_effort=reasoning_effort)
        with _TASKS_LOCK:
            entry = _find_entry_locked(task_id)
        if entry is None:
            entry = await self._cold_recover_entry(task_id)
        task = entry.task
        if not behavior_of(task.kind).supports_message_append:
            raise ValueError(behavior_of(task.kind).reject_append_text())
        # 镜像查重：重放窗口内（命令重认领）已在执行镜像队列的消息不重复入队
        with entry.pending_lock:
            if any(pm.message_id == user_message_id for pm in entry.pending_messages):
                return task.to_dict()
        if task.status.is_terminal and task.status not in REVIVABLE_END_STATES:
            # 消费拒绝：受理与消费之间任务转为不可续——行翻转 dropped，
            # 不留滞留（消费端权威；受理端的容量/校验只是咨询性快速失败）
            from noesis.runtime.main_loop import run_on_main_loop

            run_on_main_loop(
                SubagentSessionPort.flip_pending_message_dropped(user_message_id),
                name=f"bg-pending-drop:{user_message_id}",
            )
            raise ValueError(f"任务已结束（{task.status.value}），无法追加消息")
        child_id = task.child_session_id or task.task_id
        pending = _PendingMessage(message_id=user_message_id, text=text, params=params)
        with _TASKS_LOCK:
            if task.status not in REVIVABLE_END_STATES:
                # running / queued：入执行镜像队列，当前 turn 结束后链式消费
                with entry.pending_lock:
                    entry.pending_messages.append(pending)
                queued = True
            else:
                queued = False
                # 复活中和：记录先前终态快照（投递失败回退用），清停止信号、
                # 取消旧协程与在飞对账任务、复位收口旗标（复活轮发自己的
                # 终态事件与通知）
                entry.prev_terminal_snapshot = {
                    "status": task.status.value,
                    "result": task.result,
                    "error": task.error,
                    "stop_reason": task.stop_reason,
                    "completed_at": task.completed_at,
                    "persist_ok": entry.terminal_persist_ok,
                    "persist_exhausted": entry.terminal_persist_exhausted,
                }
                task.status = BgTaskStatus.RUNNING
                task.result = None
                task.completed_at = None
                entry.cooperative_stop_signalled = False
                if entry.future is not None and not entry.future.done():
                    entry.future.cancel()
                entry.future = None
                if entry.stop_reconcile_task is not None and not entry.stop_reconcile_task.done():
                    entry.stop_reconcile_task.cancel()
                entry.stop_reconcile_task = None
                entry.terminal_published = False
                entry.terminal_persist_ok = False
                entry.terminal_persist_exhausted = False
                entry.terminal_notified = False
                cancel_terminal_timers(entry)
        if queued:
            _publish_task_event(task, "message-appended")
            return task.to_dict()
        if entry.turn_factory is None:
            loop = _ensure_loop()
            entry.future = _submit_isolated(
                loop, _arun_appended_turn(entry, text, user_message_id, params),
            )
            start_watchdog_timer(entry)
            _publish_task_event(task, "message-appended")
            return task.to_dict()
        try:
            _apply_turn_params(entry, params)
            task.turn_count += 1
            launch = entry.turn_factory(child_id, text, user_message_id)
            if inspect.isawaitable(launch):
                launch = await launch
        except Exception as exc:
            await settle_delivery_failure(entry, task, exc, user_message_id)
            return task.to_dict()
        with _TASKS_LOCK:
            task.run_id = str(launch.get("run_id") or "") or None
            task.assistant_message_id = str(launch.get("assistant_message_id") or "") or None
            task.projection_sequence = 0
            # 创建窗口内已受理停止：不提交执行——停止终态获胜（不回退），
            # 消息行已被 launch 采纳为该 run 的输入，随 run 停止收口
            stopped_during_launch = entry.cooperative_stop_signalled
        if not stopped_during_launch:
            loop = _ensure_loop()
            entry.future = _submit_isolated(
                loop,
                _arun(entry, initial_source={"messages": [HumanMessage(content=text)]}),
            )
            start_watchdog_timer(entry)
        _publish_task_event(task, "message-appended")
        return task.to_dict()

    # -- 冷恢复与对账重建 ---------------------------------------------

    async def _cold_recover_entry(
        self, task_ref: str, *, expect_status: Optional[str] = None,
    ) -> _TaskEntry:
        """从 DB 重建执行条目（热集 miss）。

        task_ref = bg_task_id（child session extra）或 child session id。
        仅重建可续终态（completed / cancelled）；expect_status 用于对账
        重建时校验 queued。pending 行随条目重载（队列事实在 DB）。
        """
        info = await SubagentSessionPort.load_cold_task(task_ref)
        if info is None:
            raise ValueError(_TASK_NOT_FOUND.format(task_id=task_ref))
        status = str(info.get("status") or "")
        if expect_status is not None:
            if status != expect_status:
                raise ValueError(
                    f"任务状态与对账预期不符：{status}（预期 {expect_status}）"
                )
            task_status = BgTaskStatus.QUEUED
        elif status in ("completed", "cancelled"):
            task_status = BgTaskStatus(status)
        elif status in ("failed", "timed_out"):
            raise ValueError(f"任务已结束（{status}），无法追加消息")
        else:
            raise ValueError(f"任务状态异常：{status}（执行器重建仅支持可续终态）")
        worker_factory = (
            self._cold_resolver(info.get("subagent_type"), info.get("model"))
            if self._cold_resolver is not None else None
        )
        if worker_factory is None:
            raise ValueError(
                "任务已回收且无法重建执行配方（类型未注册或未配置冷恢复解析器）"
            )
        task = BackgroundTask(
            task_id=str(info["task_id"]),
            session_id=str(info["session_id"]),
            user_id=str(info["user_id"]),
            description=str(info.get("description") or "子 Agent"),
            child_session_id=str(info["child_session_id"]),
            run_id=info.get("run_id"),
            assistant_message_id=info.get("assistant_message_id"),
            kind="subagent",
            model_id=info.get("model_id"),
            subagent_type=info.get("subagent_type"),
            status=task_status,
            result=info.get("result"),
            error=info.get("error"),
            started_at=info.get("started_at") or time.time(),
            completed_at=info.get("completed_at"),
            turn_count=int(info.get("turn_count") or 1),
        )
        entry = _TaskEntry(
            task=task,
            agent_factory=worker_factory,
            turn_factory=self._make_cold_turn_factory(str(info["user_id"])),
            recursion_limit=self._recursion_limit,
            timeout_seconds=self._task_timeout,
            session_max_concurrent=self._max_concurrent,
            max_global=self._max_global,
            stop_grace_seconds=self._stop_grace,
            stop_reconcile_seconds=self._stop_reconcile,
        )
        if task_status.is_terminal:
            # 终态事实来自 DB：回收资格三件套直接成立
            entry.terminal_published = True
            entry.terminal_persist_ok = True
            entry.terminal_notified = True
        with entry.pending_lock:
            entry.pending_messages.extend(
                _PendingMessage(
                    message_id=row.get("message_id"),
                    text=str(row.get("text") or ""),
                    params=_TurnParams(
                        model_id=row.get("model_id"),
                        reasoning_effort=row.get("reasoning_effort"),
                    ),
                )
                for row in info.get("pending") or []
            )
        with _TASKS_LOCK:
            _TASKS.setdefault(task.task_id, entry)
        return entry

    def _make_cold_turn_factory(self, user_id: str) -> Callable[..., Any]:
        """冷恢复条目的 run 创建工厂（user_id 来自 DB 事实，非装配闭包）。

        经 run_on_main_loop 在主 loop 执行：pg_manager 连接池绑定主 loop，
        本工厂在隔离 loop 上被调用——直连会触发跨 loop 连接错误。
        """
        from noesis.agents.background.ports import SubagentSessionPort

        async def _factory(
            child_session_id: str, message: str, user_message_id: Optional[str] = None,
        ) -> dict[str, str]:
            from noesis.storage.postgres.manager import pg_manager
            from noesis.runtime.main_loop import run_on_main_loop

            async def _launch() -> dict[str, str]:
                async with pg_manager.get_async_session_context() as child_db:
                    launch = await SubagentSessionPort.create_turn_run(
                        session_id=child_session_id,
                        user_id=user_id,
                        message=message,
                        user_message_id=user_message_id,
                        db=child_db,
                    )
                    return launch.to_dict()

            future = run_on_main_loop(
                _launch(), name=f"subagent-turn-launch:{child_session_id}",
            )
            if future is None:
                raise RuntimeError("主 loop 不可用，追加消息 run 创建失败")
            return await asyncio.wrap_future(future)

        return _factory

    async def restore_queued(self, specs: list[dict[str, Any]]) -> int:
        """对账后重建排队队列（仅 child run 行；shell 执行环境不持久化不重建）。

        specs 按落库 created_at 升序给全量 queued child run；会话内 FIFO
        与全局唤醒序复用既有 drain（唤醒候选按 created_at 全局升序）。
        """
        restored = 0
        for spec in sorted(specs, key=lambda s: s.get("created_at") or 0):
            child_session_id = str(spec.get("child_session_id") or "")
            if not child_session_id:
                continue
            try:
                entry = await self._cold_recover_entry(
                    child_session_id, expect_status="queued",
                )
            except Exception:  # noqa: BLE001
                logger.opt(exception=True).error(
                    "bg task queue rebuild failed child_session_id={}",
                    child_session_id,
                )
                continue
            with _TASKS_LOCK:
                if entry.task.status == BgTaskStatus.QUEUED:
                    _PENDING_QUEUES.setdefault(entry.task.session_id, []).append(entry)
            restored += 1
        if restored:
            logger.info("bg task queue restored count={}", restored)
            _drain_restored()
        return restored

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


def _drain_restored() -> None:
    """对账重建后触发全局排队唤醒（复用既有两级准入与 drain 逻辑）。"""
    from noesis.agents.background.jobs.registry import _drain_all_sessions

    _drain_all_sessions()


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
    # 单一异步追加消息入口（校验折叠在锁内前置；同步/异步双版本的
    # 端口漂移事故见 ports.py 同名注释）。查询族带 DB 兜底（热集 miss
    # → DB 投影），经 default() 取最近装配实例。签名与 ports.py 的
    # ExecutorPort 转发逐参一致（tests/test_port_contracts.py 钉住）。
    @staticmethod
    async def deliver_message(
        task_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> dict[str, Any]:
        return await BackgroundTaskExecutor.default().deliver_message(
            task_id=task_id,
            message=message,
            user_message_id=user_message_id,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
        )

    @staticmethod
    async def check_with_fallback(task_id: str) -> Optional[dict[str, Any]]:
        return await BackgroundTaskExecutor.default().check_with_fallback(task_id)

    @staticmethod
    async def list_with_fallback(session_id: str) -> list[dict[str, Any]]:
        return await BackgroundTaskExecutor.default().list_with_fallback(session_id)

    @staticmethod
    async def cancel_with_fallback(task_id: str) -> dict[str, Any]:
        return await BackgroundTaskExecutor.default().cancel_with_fallback(task_id)

    @staticmethod
    async def restore_queued(specs: list[dict[str, Any]]) -> int:
        return await BackgroundTaskExecutor.default().restore_queued(specs)

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
