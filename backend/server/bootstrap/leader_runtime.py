"""角色化启动对账与装配（worker-role-split Phase 3：自 main.py lifespan 下沉）。

对账按「谁持有状态谁对账」拆两组：

- **control 对账**（control × 1，advisory lock 防双开）：主 run 阶段化标记终态
  （未碰世界重置 queued / 已碰世界标记为 interrupted）、定时任务遗留 run。
- **worker 对账**（每个 worker 启动时）：子代理 run、后台 shell 任务、
  queued 子任务重建（executor 热集在本进程）、遗留 claimed 命令重置
  （必须先于命令消费者启动）、未送达通知装载。

顺序约束是安全不变式：``claimed 命令重置 → 命令消费者启动``（换主窗口
不误翻排队任务的消息）、``queued 重建 → dispatcher.start``（重建后 drain
由 dispatcher 驱动）。清单结构与顺序由 ``tests/test_leader_runtime_order.py``
钉住。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable

from noesis.runtime.logging import logger


def _control_reconcile_steps(recovery_db) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    """control 面对账：主 run（阶段化）+ 定时任务。"""

    async def _main_runs() -> int:
        from noesis.services.run_recovery_service import RunRecoveryService
        from noesis.services.run_service import RunService

        await RunRecoveryService.recover_orphaned_runs(
            recovery_db,
            heartbeat_lease_ms=int(RunService.HEARTBEAT_LEASE_TTL_SECONDS * 1000),
        )
        return 0

    async def _scheduled_task_runs() -> int:
        from noesis.services.scheduled_task_service import ScheduledTaskService

        interrupted = await ScheduledTaskService.reconcile_interrupted_runs(recovery_db)
        if interrupted:
            logger.warning("定时任务对账：{} 个遗留 run 已标记为 interrupted", interrupted)
        return interrupted

    return [
        ("main_runs", _main_runs),
        ("scheduled_task_runs", _scheduled_task_runs),
    ]


def _worker_reconcile_steps(recovery_db) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    """worker 面对账：executor 热集相关 + 命令重置 + 通知装载。"""

    async def _subagent_runs() -> int:
        from noesis.services.subagent_session_service import SubagentSessionService

        orphaned = await SubagentSessionService.reconcile_orphaned_runs(recovery_db)
        if orphaned:
            logger.warning("子 Agent 对账：{} 个遗留 run 已标记为中断", orphaned)
        return orphaned

    async def _shell_jobs() -> int:
        from noesis.services.bg_shell_job_service import BgShellJobService

        orphaned = await BgShellJobService.reconcile_orphaned(recovery_db)
        if orphaned:
            logger.warning("后台命令对账：{} 个非终态 shell 任务已标记为 cancelled", orphaned)
        return orphaned

    async def _rebuild_queued() -> int:
        from noesis.agents.background.ports import ExecutorPort
        from noesis.services.subagent_session_service import SubagentSessionService

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

    async def _restore_notifications() -> int:
        from noesis.services.bg_notification_store import (
            restore_undelivered_notifications,
        )

        restored = await restore_undelivered_notifications(recovery_db)
        if restored:
            logger.info("后台通知启动恢复：{} 条未送达通知已装载", restored)
        return restored

    return [
        ("subagent_runs", _subagent_runs),
        ("shell_jobs", _shell_jobs),
        ("rebuild_queued", _rebuild_queued),
        ("reset_claimed_commands", _reset_claimed_commands),
        ("restore_notifications", _restore_notifications),
    ]


async def run_reconcile_group(steps: list[tuple[str, Callable[[], Awaitable[Any]]]]) -> None:
    """顺序执行对账组，异常大声失败（启动期半初始化比崩溃更难排障）。"""
    for _name, step in steps:
        await step()


def _names(steps_factory: Callable[..., list[tuple[str, Any]]]) -> list[str]:
    from unittest.mock import MagicMock

    return [name for name, _ in steps_factory(MagicMock())]


# 对账步骤名序列（单一事实源：从步骤工厂推导；改步骤清单测试同步红）
CONTROL_RECONCILE_ORDER = _names(_control_reconcile_steps)
WORKER_RECONCILE_ORDER = _names(_worker_reconcile_steps)


# 周期对账间隔：lease_ttl（60s）的一半——僵尸 run 的终态处理延迟上界
# = lease_ttl 超时判定 + 本间隔。产品逻辑常量（非部署参数）：与心跳
# 租约配套定档，两部署实例该值理应相同。
PERIODIC_RECONCILE_INTERVAL_SECONDS = 30.0


async def start_control_singletons(*, resources: AsyncExitStack) -> None:
    """control 专属 singleton：周期对账、调度器、信令通道、记忆任务。

    advisory lock 防双开由调用方（entries）在启动前获取。周期对账
    （design §2.2）：运行期 heartbeat 超时的僵尸 run 由本循环标记终态/
    重置，不等 control 重启。
    """
    from noesis.config.env import MessagingConfig
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

    # 周期对账：僵尸判定（heartbeat 超时）+ 阶段化分流，与启动对账共用
    # 同一步骤工厂——单一人（谁持有状态谁对账的 control 面）。
    import asyncio

    async def _periodic_reconcile() -> None:
        from noesis.storage.postgres.manager import pg_manager

        while True:
            await asyncio.sleep(PERIODIC_RECONCILE_INTERVAL_SECONDS)
            try:
                async with pg_manager.get_async_session_context() as db:
                    await run_reconcile_group(_control_reconcile_steps(db))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("control 周期对账失败")

    reconcile_task = asyncio.create_task(
        _periodic_reconcile(), name="control-periodic-reconcile"
    )

    def _cancel_reconcile() -> None:
        reconcile_task.cancel()

    resources.callback(_cancel_reconcile)
