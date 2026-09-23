"""leader 运行时装配与晋升对账（自 main.py lifespan 下沉）。

两类职责，顺序约束是安全不变式：

1. **晋升回调**（``build_promotion_callback``）：leader 面装配（run 事件
   桥 / 命令消费者）+ 四段对账 + 排队重建。对账顺序由
   ``RECONCILE_ORDER`` 声明——命令重置先于命令消费、queued 重建先于
   dispatcher.start，乱序会在换主窗口丢消息或重复消费。
2. **singleton 启动**（``start_leader_singletons``）：dispatcher / 调度
   器 / 信令通道 / 记忆任务——仅 leader 运行，follower 不注册。

lifespan 只保留调用；顺序约束由 ``_reconcile_steps`` 的清单结构与
tests/test_leader_runtime_order.py 钉住。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable

from noesis.runtime.logging import logger
from noesis.config.env import DistributedRunsConfig, MessagingConfig
from noesis.services.run_command_service import RunCommandConsumer


# 晋升对账步骤（执行顺序即列表顺序）。每步 (名称, 协程工厂)。
# 顺序约束（安全不变式）：
#   claimed 命令重置 → 命令消费者启动（换主窗口不误翻排队任务的消息）
#   queued 重建      → dispatcher.start（重建后 drain 由 dispatcher 驱动）
def _reconcile_steps(recovery_db, *, token_term: int) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    from noesis.services.subagent_session_service import SubagentSessionService

    async def _main_runs() -> int:
        from noesis.services.run_recovery_service import RunRecoveryService

        await RunRecoveryService.recover_orphaned_runs(
            recovery_db, current_leader_term=token_term
        )
        return 0

    async def _subagent_runs() -> int:
        orphaned = await SubagentSessionService.reconcile_orphaned_runs(recovery_db)
        if orphaned:
            logger.warning("子 Agent 对账：{} 个遗留 run 已标记为中断", orphaned)
        return orphaned

    async def _shell_jobs() -> int:
        from noesis.services.bg_shell_job_service import BgShellJobService

        orphaned = await BgShellJobService.reconcile_orphaned(recovery_db)
        if orphaned:
            logger.warning("后台命令对账：{} 个非终态 shell 任务已收口为 cancelled", orphaned)
        return orphaned

    async def _rebuild_queued() -> int:
        from noesis.agents.background.ports import ExecutorPort

        specs = await SubagentSessionService.list_queued_subagent_runs(recovery_db)
        if not specs:
            return 0
        restored = await ExecutorPort.restore_queued(specs)
        logger.info("后台任务排队重建：{} 个 queued 任务已恢复", restored)
        return restored

    async def _reset_claimed_commands() -> int:
        from noesis.repositories.agent_run_command_repository import (
            AgentRunCommandRepository,
        )

        await AgentRunCommandRepository(recovery_db).reset_all_claimed()
        return 0

    async def _scheduled_task_runs() -> int:
        from noesis.services.scheduled_task_service import ScheduledTaskService

        interrupted = await ScheduledTaskService.reconcile_interrupted_runs(recovery_db)
        if interrupted:
            logger.warning("定时任务对账：{} 个遗留 run 已收口为 interrupted", interrupted)
        return interrupted

    async def _restore_notifications() -> int:
        from noesis.services.bg_notification_store import (
            restore_undelivered_notifications,
        )

        restored = await restore_undelivered_notifications(recovery_db)
        if restored:
            logger.info("后台通知启动恢复：{} 条未送达通知已装载", restored)
        return restored

    return [
        ("main_runs", _main_runs),
        ("subagent_runs", _subagent_runs),
        ("shell_jobs", _shell_jobs),
        ("rebuild_queued", _rebuild_queued),
        ("reset_claimed_commands", _reset_claimed_commands),
        ("scheduled_task_runs", _scheduled_task_runs),
        ("restore_notifications", _restore_notifications),
    ]


def build_promotion_callback(
    *,
    elector,
    run_bus,
    run_manager,
    resources: AsyncExitStack,
    leader_components: dict,
) -> Callable[[Any], Awaitable[None]]:
    """构造 leader 晋升回调（含进程启动首例）。

    dispatcher / command consumer / 信令与 run 事件桥随晋升启动；重入
    （运行中切主后本进程晋升）时先跑对账再起消费者——旧 term 遗留由
    对账步骤收口。命令消费者的 start 挂在对账序列之后（顺序约束见
    模块 docstring）。
    """

    async def _on_promotion(token) -> None:
        run_manager.attach_bus(run_bus)
        from noesis.agents.background.jobs import events as bg_run_events

        bg_run_events.configure_run_event_bridge(run_bus, lambda: elector.token)
        if "command_consumer" not in leader_components:
            consumer = RunCommandConsumer(
                bus=run_bus,
                scan_interval_seconds=DistributedRunsConfig.command_scan_interval_seconds,
                retention_days=DistributedRunsConfig.command_retention_days,
                # worker-role-split 过渡：单进程（memory 模式 / redis leader）
                # 持有全部 run，不分片；Phase 3 入口拆分后由 worker 注入
                # 进程内持有判定
                shard_filter=None,
            )
            leader_components["command_consumer"] = consumer
            resources.push_async_callback(consumer.stop)
        # 四段对账 + 排队重建：顺序执行，异常大声失败（启动期半初始化
        # 比崩溃更难排障）
        from noesis.storage.postgres.manager import pg_manager as _pgm

        async with _pgm.get_async_session_context() as recovery_db:
            for name, step in _reconcile_steps(recovery_db, token_term=token.term):
                await step()
            consumer = leader_components.get("command_consumer")
            if consumer is not None:
                await consumer.start()

    return _on_promotion


async def start_leader_singletons(
    *,
    dispatcher,
    resources: AsyncExitStack,
) -> None:
    """leader 专属 singleton：dispatcher / 调度器 / 信令通道 / 记忆任务。

    后台任务执行面（executor/隔离循环）与 shutdown_bg_subagents 也仅
    leader 需要——follower 没有注册表可停。
    """
    from noesis.services.scheduled_task_scheduler import (
        start_scheduled_task_scheduler,
        stop_scheduled_task_scheduler,
    )
    from noesis.services.channels.telegram_runtime import (
        start_telegram_runtime,
        stop_telegram_runtime,
    )
    from noesis.memory.consolidation import (
        start_memory_consolidator,
        stop_memory_consolidator,
    )
    from noesis.memory.extraction import start_memory_sweeper, stop_memory_sweeper

    await dispatcher.start()
    start_scheduled_task_scheduler()
    resources.push_async_callback(stop_scheduled_task_scheduler)
    start_telegram_runtime()
    resources.push_async_callback(stop_telegram_runtime)
    if MessagingConfig.feishu_runtime_enabled:
        try:
            from noesis.services.channels.feishu_runtime import (
                start_feishu_runtime,
                stop_feishu_runtime,
            )

            start_feishu_runtime()
            resources.push_async_callback(stop_feishu_runtime)
        except ImportError as exc:
            logger.error(
                "飞书已启用但 lark-oapi 未安装（uv sync --extra feishu），通道不启动: {}",
                exc,
            )
    else:
        logger.info("feishu runtime disabled (messaging.feishu_runtime_enabled=false)")
    await start_memory_sweeper()
    resources.push_async_callback(stop_memory_sweeper)
    await start_memory_consolidator()
    resources.push_async_callback(stop_memory_consolidator)


# 对账步骤名序列（单一事实源：从步骤工厂推导；改步骤清单测试同步红）
def _names() -> list[str]:
    from unittest.mock import MagicMock

    return [name for name, _ in _reconcile_steps(MagicMock(), token_term=0)]


RECONCILE_ORDER = _names()
