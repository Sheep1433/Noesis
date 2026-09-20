"""终态收口：终态规格、认领裁决、结案链与终态定时器族。

五条并发契约的家：先到终态获胜 / 终态不可覆写 / 落库失败不发终态 /
收口不依赖协程配合（对账兜底）/ 失败也释放槽位。watchdog、停止宽限、
对账三族定时器都在本模块，经 jobs.loop 挂载到隔离循环。
"""
from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Optional

from noesis.agents.background import notifications
from noesis.agents.background.kinds import behavior_of
from noesis.chat.runs import RunStatus
from noesis.runtime.logging import logger

from noesis.agents.background.jobs.events import _publish_run_event, _publish_task_event
from noesis.agents.background.jobs.loop import _ensure_loop, _loop_timer_arm
from noesis.agents.background.jobs.registry import (
    _TASKS_LOCK,
    _TaskEntry,
    _drain_session_queue,
    reclaim_terminal_entries,
)
from noesis.agents.background.jobs.state import (
    _PARTIAL_OUTPUT_PREFIX,
    _PARTIAL_RESULT_MAX_CHARS,
    BgTaskStatus,
    BackgroundTask,
)

if TYPE_CHECKING:
    from noesis.agents.background.subagent.kernel import _TurnOutcome


def _notify_preview(task: BackgroundTask) -> Optional[str]:
    """通知预览：取消/超时携带部分产出内容本身（标注前缀不占预览预算）。"""
    result = task.result
    if result and result.startswith(_PARTIAL_OUTPUT_PREFIX):
        return result[len(_PARTIAL_OUTPUT_PREFIX):].lstrip() or None
    return result or task.error

def _notify_terminal(entry: "_TaskEntry", task: BackgroundTask) -> None:
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
    entry.terminal_notified = True
    reclaim_terminal_entries()

def _schedule_continuation(task: BackgroundTask) -> None:
    """终态后尝试唤醒主 Agent（对齐 dsh 父会话追加消息语义的 run 级等价物）。

    无活跃 run 时自动创建 continuation run；调度回主 loop（DB 引擎与
    RunManager 绑定主 loop）。所有终态都触发——仅认 COMPLETED 的旧门控
    在无人值守会话（定时任务）下意味着失败/超时的交付永不回到父 Agent
    （生产事故根因：子任务 900s 超时后父 Agent 永久等待）。空转风险不靠
    此处门控，由 continuation 服务的 60s 去抖 + 连续唤醒上限兜底。
    经 schedule_maybe_continue 去抖：窗口内多个终态合并为一次唤醒，
    避免每个任务终态各产生一个重复发送全量上下文的 run。
    """
    from noesis.runtime.main_loop import run_on_main_loop
    from noesis.agents.background.ports import ContinuationPort

    run_on_main_loop(
        ContinuationPort.schedule_maybe_continue(task.session_id, task.user_id),
        name=f"bg-continue:{task.task_id}",
    )

def _try_transition(task: BackgroundTask, next_status: BgTaskStatus) -> bool:
    """非终态状态写入收口（RUNNING 恢复）：
    终态不得被覆写（乐观停止受理即落 CANCELLED，执行侧的恢复/
    追加消息 写入不得复活已停任务）。

    执行侧恢复/审批写入经此在同一把锁下复查——互斥关闭「检查后写入」
    窗口（否则停止被覆写丢失，追加消息 甚至反向新开 run）。
    返回 False = 任务已终态（多为停止受理），调用方走取消收尾。
    """
    with _TASKS_LOCK:
        if task.status.is_terminal:
            return False
        task.status = next_status
        return True


# 停止族终态（cancelled / timed_out）：晚到规格降级的目标族
_STOP_TERMINALS: frozenset[BgTaskStatus] = frozenset(
    {BgTaskStatus.CANCELLED, BgTaskStatus.TIMED_OUT}
)

# 可冷恢复续聊的终态：停止是乐观终态且只终止执行（执行/意图分离），
# 排队与后续的 追加消息 意图保留——completed / cancelled 均可经
# deliver_message 同 thread 续跑；failed / timed_out 语义上不可续
REVIVABLE_END_STATES: frozenset[BgTaskStatus] = frozenset(
    {BgTaskStatus.COMPLETED, BgTaskStatus.CANCELLED}
)

@dataclass(frozen=True)
class TaskTerminal:
    """终态规格：一条收口路径一份规格，落库/事件/通知语义集中在 settle_task。

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

def cancel_terminal_timers(entry: _TaskEntry) -> None:
    """终态定时器统一拆除：watchdog / 停止宽限 / 硬杀对账。"""
    cancel_watchdog_timer(entry)
    cancel_stop_grace_timer(entry)
    cancel_reconcile_timer(entry)

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
    """构造 mark_terminal 协程（_persist_run_terminal / settle_task_sync 共用）。"""
    from noesis.agents.background.ports import SubagentSessionPort

    return SubagentSessionPort.mark_terminal(
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

async def _persist_terminal_with_retry(
    entry: "_TaskEntry",
    task: BackgroundTask,
    terminal: TaskTerminal,
    persist_timeout: Optional[float],
) -> None:
    """终态落库有界重试（主链路同款 persistence 超时预算）。

    成功置 terminal_persist_ok；预算耗尽置 terminal_persist_exhausted——
    事件与通知照发（否则会话队列停摆），DB 投影按落库事实回答并携带
    「终态落库失败」标注；条目在耗尽后允许回收（不伪造终态，不无界滞留）。
    """
    from noesis.config.env import StreamConfig

    deadline = time.monotonic() + StreamConfig.persistence_timeout_seconds
    while True:
        try:
            if persist_timeout is not None:
                await asyncio.wait_for(
                    _persist_run_terminal(task, terminal), timeout=persist_timeout,
                )
            else:
                await _persist_run_terminal(task, terminal)
        except Exception:
            if time.monotonic() >= deadline:
                with _TASKS_LOCK:
                    entry.terminal_persist_exhausted = True
                logger.opt(exception=True).error(
                    "bg task terminal persist exhausted task_id={} finish_reason={}",
                    task.task_id,
                    terminal.finish_reason,
                )
                # 诊断位落 run 行（不改状态不伪造终态）：DB 投影据此携带
                # 「终态落库失败」标注；进程重启对账随后把遗留 run 正常收口
                if task.run_id:
                    from noesis.agents.background.ports import SubagentSessionPort
                    from noesis.runtime.main_loop import run_on_main_loop

                    run_on_main_loop(
                        SubagentSessionPort.mark_terminal_persist_exhausted(task.run_id),
                        name=f"bg-persist-exhausted:{task.run_id}",
                    )
                return
            await asyncio.sleep(0.2)
            continue
        with _TASKS_LOCK:
            entry.terminal_persist_ok = True
        return

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

def _publish_terminal_events(entry: "_TaskEntry", task: BackgroundTask, terminal: TaskTerminal) -> None:
    _publish_run_event(
        task, "run.finished", content=terminal.content, finish_reason=terminal.finish_reason,
    )
    _publish_task_event(task, "terminal")
    _notify_terminal(entry, task)
    # shell 任务事实行终态化（无 run_id，走独立表）
    if task.kind == "shell":
        from noesis.agents.background.ports import ShellJobPort
        from noesis.runtime.main_loop import run_on_main_loop

        run_on_main_loop(
            ShellJobPort.mark_terminal(
                task_id=task.task_id,
                status=terminal.task_status.value,
                error=terminal.error,
                result_tail=task.result,
                completed_at=task.completed_at,
            ),
            name=f"bg-shell-terminal:{task.task_id}",
        )
    # 不可续终态（failed / timed_out）的未消费追加消息永无消费者：
    # 收口即翻转 dropped（可续终态 completed / cancelled 保留，供冷恢复重载）
    if terminal.task_status in (BgTaskStatus.FAILED, BgTaskStatus.TIMED_OUT) and task.child_session_id:
        from noesis.agents.background.ports import SubagentSessionPort
        from noesis.runtime.main_loop import run_on_main_loop

        run_on_main_loop(
            SubagentSessionPort.flip_pending_messages_dropped(task.child_session_id),
            name=f"bg-pending-drop-all:{task.child_session_id}",
        )

async def settle_task(
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
    cancel_terminal_timers(entry)
    await _persist_terminal_with_retry(entry, task, terminal, persist_timeout)
    if _claim_terminal_publish(entry):
        _publish_terminal_events(entry, task, terminal)
    return True

def settle_task_sync(entry: _TaskEntry, terminal: TaskTerminal) -> bool:
    """同步终态收口（cancel 即时分支 / shell 超时 / 沙箱销毁）。

    与 settle_task 同语义，但落库 fire-and-forget——调用线程不可等待，
    主 loop 异步落库失败只在主 loop 侧日志可见。
    """
    task = entry.task
    terminal = _accept_terminal(entry, terminal)
    cancel_terminal_timers(entry)
    if task.run_id:
        from noesis.runtime.main_loop import run_on_main_loop

        run_on_main_loop(
            _persist_terminal_with_retry(entry, task, terminal, None),
            name=f"subagent-terminal:{task.run_id}",
        )
    if _claim_terminal_publish(entry):
        _publish_terminal_events(entry, task, terminal)
    return True

async def settle_stop(
    entry: _TaskEntry,
    task: BackgroundTask,
    outcome: Optional[_TurnOutcome],
) -> None:
    """协作停止 / 硬杀的停止收口编排：部分成果回收 + 唯一终态收口。

    - 部分成果以落库投影为权威来源（覆盖全部轮次与硬杀边界前产出），
      无标准 run（测试）退回当前 turn 投影；写入 task.result 供通知预览
    - 硬杀（outcome=None）沿用 run 已积累快照（content=None 语义）
    - 若本协程在收口途中被卡死，settle_orphaned_task 已接管发布，晚到重入
      只补落库不重发事件（归属权在 settle_task 内部认领）
    """
    # 入口先拆定时器：宽限/看门狗不得在部分成果回收（跨 loop DB 往返，
    # 大投影可达秒级）期间硬杀收口协程本身——否则收口被打断后只能等
    # 对账兜底，且回收的 content/usage 载荷全部丢失
    from noesis.agents.background.subagent.kernel import (  # 延迟导入避免 settle↔agent 环
        _collect_persisted_text,
        _turn_text_parts,
    )

    cancel_terminal_timers(entry)
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
    await settle_task(entry, terminal)
    logger.info(
        "bg task stopped cooperatively task_id={} reason={} steps={} duration={:.1f}s partial={}",
        task.task_id,
        terminal.stop_reason,
        task.step_count,
        (task.completed_at or time.time()) - task.started_at,
        bool(task.result),
    )

async def settle_orphaned_task(entry: _TaskEntry) -> None:
    """硬杀对账的强制终态：协程未按约收口时不依赖其配合直接落终态。

    与 settle_stop 的硬杀分支同语义（content=None 沿用已积累快照），
    但不做部分成果回收——被卡死的协程可能正持有投影 builder。落库有界
    等待：对账路径自身不允许无限等待（否则只是把卡死换了个位置）。
    """
    task = entry.task
    if entry.terminal_published:
        return
    logger.error(
        "bg task stop reconcile: hard cancel 后协程未收口，强制终态 task_id={}",
        task.task_id,
    )
    await settle_task(
        entry, _stop_terminal(entry), persist_timeout=entry.stop_reconcile_seconds,
    )
    logger.info(
        "bg task force finalized task_id={} reason={} steps={}",
        task.task_id,
        task.stop_reason or "cancelled",
        task.step_count,
    )


async def settle_delivery_failure(
    entry: "_TaskEntry",
    task: BackgroundTask,
    exc: BaseException,
    user_message_id: Optional[str] = None,
) -> None:
    """追加消息投递失败收口：不终态化任务。

    - 消息行翻转 dropped（幂等：仅仍处 pending 标记的行受影响；已被
      launch 采纳的行不受影响）
    - 冷恢复/复活路径回退先前终态并恢复完整收口态（result/completed_at/
      published/notified/落库旗标）——不重发终态事件
    - 冷恢复窗口内受理的停止终态获胜：不回退、不触碰已受理终态
      （乐观终态契约优先于回退）
    """
    from noesis.agents.background.ports import SubagentSessionPort
    from noesis.runtime.main_loop import run_on_main_loop

    if user_message_id:
        flip_future = run_on_main_loop(
            SubagentSessionPort.flip_pending_message_dropped(user_message_id),
            name=f"bg-pending-drop:{user_message_id}",
        )
        if flip_future is not None:
            # 错误路径可等待：保证调用方拿到的回退快照与 DB 翻转的先后一致
            await asyncio.wrap_future(flip_future)
    snapshot = entry.prev_terminal_snapshot
    with _TASKS_LOCK:
        if task.status.is_terminal or snapshot is None:
            # 停止获胜（新终态已受理）或无先前终态可回退：只落 dropped
            return
        task.status = BgTaskStatus(snapshot["status"])
        task.result = snapshot.get("result")
        task.error = snapshot.get("error")
        task.stop_reason = snapshot.get("stop_reason")
        task.completed_at = snapshot.get("completed_at")
        entry.terminal_published = True
        entry.terminal_persist_ok = bool(snapshot.get("persist_ok"))
        entry.terminal_persist_exhausted = bool(snapshot.get("persist_exhausted"))
        entry.terminal_notified = True
        cancel_terminal_timers(entry)
    logger.opt(exception=True).error(
        "bg task message delivery failed task_id={} restored_status={}",
        task.task_id,
        snapshot.get("status"),
    )


def start_stop_grace_timer(entry: _TaskEntry) -> None:
    """停止宽限 watchdog：宽限内静止边界未到达即回退硬杀。

    先摘旧句柄——cancel 与超时协作路径并发触发时不得泄漏定时器。
    """
    cancel_stop_grace_timer(entry)
    cancel_watchdog_timer(entry)
    _loop_timer_arm(entry, "stop_grace_handle", entry.stop_grace_seconds, _on_stop_grace_timeout)


def cancel_stop_grace_timer(entry: _TaskEntry) -> None:
    if entry.stop_grace_handle is not None:
        entry.stop_grace_handle.cancel()
        entry.stop_grace_handle = None


def cancel_reconcile_timer(entry: _TaskEntry) -> None:
    if entry.stop_reconcile_handle is not None:
        entry.stop_reconcile_handle.cancel()
        entry.stop_reconcile_handle = None


def start_reconcile_timer(entry: _TaskEntry) -> None:
    """硬杀后对账 watchdog：协程未按约收口时强制落终态。

    先摘旧句柄——宽限超时重复触发时不得泄漏定时器。
    """
    cancel_reconcile_timer(entry)
    _loop_timer_arm(
        entry, "stop_reconcile_handle", entry.stop_reconcile_seconds, _on_stop_reconcile_timeout,
    )


def _on_stop_grace_timeout(entry: _TaskEntry) -> None:
    """停止宽限超时：回退硬杀（CancelledError → settle_stop(outcome=None)）。"""
    entry.stop_grace_handle = None
    if not entry.cooperative_stop_signalled:
        return
    logger.warning(
        "bg task stop grace exceeded, hard cancel task_id={}",
        entry.task.task_id,
    )
    if entry.future is not None and not entry.future.done():
        # cancel 返回 False 仅发生在 future 已完成——协程已自行收尾
        entry.future.cancel()
    # 终态不依赖被取消协程的配合：CancelledError 可能在深层执行链被吸收
    # （曾出现硬取消后 except CancelledError 分支未执行、run 永久 RUNNING），
    # 对账 watchdog 在 reconcile 窗口后强制落终态
    start_reconcile_timer(entry)


def _on_stop_reconcile_timeout(entry: _TaskEntry) -> None:
    """硬杀后对账：协程未按约收口时强制落终态。"""
    entry.stop_reconcile_handle = None
    if entry.terminal_published:
        return
    task = entry.task
    logger.error(
        "bg task stop reconcile due task_id={} status={}（CancelledError 未按约传播至收口）",
        task.task_id,
        task.status.value,
    )
    loop = _ensure_loop()
    # 强引用挂在 entry 上：裸 create_task 无引用时可能被 GC 中途回收
    entry.stop_reconcile_task = loop.create_task(settle_orphaned_task(entry))


def start_watchdog_timer(entry: _TaskEntry) -> None:
    cancel_watchdog_timer(entry)
    _loop_timer_arm(entry, "watchdog_handle", entry.timeout_seconds, _on_task_timeout)


def cancel_watchdog_timer(entry: _TaskEntry) -> None:
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
    start_stop_grace_timer(entry)

def _on_timeout_hard(entry: _TaskEntry) -> None:
    """即时超时终态（shell 专用）：硬杀 + TIMED_OUT + 通知。"""
    cancel_watchdog_timer(entry)
    if entry.future is not None and not entry.future.done():
        entry.future.cancel()
    settle_task_sync(
        entry,
        TaskTerminal(
            task_status=BgTaskStatus.TIMED_OUT,
            run_status=RunStatus.ERROR,
            finish_reason="timeout",
            error=f"后台任务超时（{int(entry.timeout_seconds)}s）",
            stop_reason="timed_out",
        ),
    )
