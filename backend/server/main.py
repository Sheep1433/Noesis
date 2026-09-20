import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from server.exception_handlers import handle_exception
from server.middleware.csrf import CsrfMiddleware
from server.middleware.request_log_context import RequestLogContextMiddleware
from noesis.config.env import AppConfig, DistributedRunsConfig, MessagingConfig, StreamConfig
from noesis.config.checkpointer import close_checkpointer, init_checkpointer
from server.db import init_database
from noesis.storage.postgres.manager import pg_manager
from noesis.runtime.logging import logger
from server.langfuse import sync_langfuse_env_from_app_config
from server.api import (
    user_router,
    chat_router,
    knowledge_base_router,
    skill_router,
    chat_attachment_router,
    model_router,
    auth_router,
    mcp_router,
    user_settings_router,
    user_llm_router,
    settings_router,
)
from noesis.knowledge.runtime import init_knowledge_base, close_knowledge_base
from noesis.agents.backends.sandbox_lifecycle import shutdown_sandboxes
from noesis.agents.background import (
    shutdown as shutdown_bg_subagents,
)
from noesis.runtime.main_loop import capture_main_loop
from server.wiring import wire_runtime_observability
from noesis.services.scheduled_task_scheduler import (
    start_scheduled_task_scheduler,
    stop_scheduled_task_scheduler,
)
from noesis.services.channels.telegram_runtime import start_telegram_runtime, stop_telegram_runtime
from noesis.memory.consolidation import (
    start_memory_consolidator,
    stop_memory_consolidator,
)
from noesis.memory.extraction import start_memory_sweeper, stop_memory_sweeper
from server.bootstrap.kb import sync_existing_kb_collection_configs
from noesis.services.run_recovery_service import RunRecoveryService
from noesis.services.run_service import run_manager, run_bus
from noesis.services.leader_elector import LeaderElector
from noesis.services.run_dispatcher import RunDispatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 组合根：注册 agents.background.ports 实现（幂等），
    # 首个请求/后台任务前端口必须就绪
    from noesis.services.runtime_ports import register_runtime_ports

    register_runtime_ports()

    logger.info(f'⏰️ {AppConfig.app_name}开始启动')
    capture_main_loop()
    sync_langfuse_env_from_app_config()
    wire_runtime_observability()
    async with AsyncExitStack() as resources:
        resources.push_async_callback(pg_manager.close)
        # ---- migration lock（独立 key）：多 worker 并发启动时串行跑 migration。
        # 执行锁已移到 migration 之后（P3 起 follower 也需完成 migration 才能 ready）。
        await pg_manager.acquire_migration_lock()
        try:
            await init_database()
        finally:
            await pg_manager.release_migration_lock()

        leader_components: dict = {}

        async def _on_promotion(token) -> None:
            """leader 晋升回调（含进程启动首例）：leader 面装配 + recovery。

            dispatcher / command consumer / 信令与 run 事件桥 / singleton
            runtime 都随晋升启动；重入（运行中切主后本进程晋升）时先跑
            recovery 再起 dispatcher——旧 term 遗留已由四段对账收口。
            """
            from noesis.services.run_command_service import RunCommandConsumer

            run_manager.attach_bus(run_bus, token_provider=lambda: elector.token)
            from noesis.agents.background.jobs import events as bg_run_events

            bg_run_events.configure_run_event_bridge(run_bus, lambda: elector.token)
            if "command_consumer" not in leader_components:
                consumer = RunCommandConsumer(
                    bus=run_bus,
                    token_provider=lambda: elector.token,
                    scan_interval_seconds=DistributedRunsConfig.command_scan_interval_seconds,
                    retention_days=DistributedRunsConfig.command_retention_days,
                )
                leader_components["command_consumer"] = consumer
                resources.push_async_callback(consumer.stop)
            # ---- 晋升对账（四段）：主 Run → 子代理 Run → 定时任务 → 通知装载
            async with pg_manager.get_async_session_context() as recovery_db:
                await RunRecoveryService.recover_orphaned_runs(
                    recovery_db, current_leader_term=token.term
                )
                from noesis.services.subagent_session_service import SubagentSessionService

                orphaned_subagents = await SubagentSessionService.reconcile_orphaned_runs(recovery_db)
                if orphaned_subagents:
                    logger.warning("子 Agent 对账：{} 个遗留 run 已标记为中断", orphaned_subagents)
                from noesis.services.bg_shell_job_service import BgShellJobService

                orphaned_shell = await BgShellJobService.reconcile_orphaned(recovery_db)
                if orphaned_shell:
                    logger.warning("后台命令对账：{} 个非终态 shell 任务已收口为 cancelled", orphaned_shell)
                # 排队重建（仅 child run 行）：queued 任务重启后继续执行，
                # 按 created_at 升序重建进程内队列并触发 drain
                from noesis.agents.background.ports import ExecutorPort

                queued_specs = await SubagentSessionService.list_queued_subagent_runs(recovery_db)
                if queued_specs:
                    restored = await ExecutorPort.restore_queued(queued_specs)
                    logger.info("后台任务排队重建：{} 个 queued 任务已恢复", restored)
                # 晋升对账：遗留 claimed 命令重置回 pending（旧 leader 认领必然
                # 未完成），随后才启动命令消费——对账先于消费，换主窗口排队
                # 任务的追加消息不被误翻转
                from noesis.repositories.agent_run_command_repository import (
                    AgentRunCommandRepository,
                )

                await AgentRunCommandRepository(recovery_db).reset_all_claimed()
                consumer = leader_components.get("command_consumer")
                if consumer is not None:
                    await consumer.start()
                from noesis.services.scheduled_task_service import ScheduledTaskService

                interrupted_runs = await ScheduledTaskService.reconcile_interrupted_runs(recovery_db)
                if interrupted_runs:
                    logger.warning("定时任务对账：{} 个遗留 run 已收口为 interrupted", interrupted_runs)
                from noesis.services.bg_notification_store import (
                    restore_undelivered_notifications,
                )

                restored_notices = await restore_undelivered_notifications(recovery_db)
                if restored_notices:
                    logger.info("后台通知启动恢复：{} 条未送达通知已装载", restored_notices)

        # ---- Leader elector：竞争执行锁（key 不变，滚动升级期新旧互斥）并提交
        # 全局 leadership term。memory 模式第二实例 fail-fast；redis 模式未获锁
        # 的进程以 Web worker 待命 + 周期重竞选，晋升回调承载 leader 面装配
        # （task 2.3/2.4）。
        elector = LeaderElector(cluster_id=DistributedRunsConfig.cluster_id)
        if DistributedRunsConfig.backend == "redis":
            await elector.run_as_worker(on_promotion=_on_promotion)
        else:
            leadership_token = await elector.acquire()
            await _on_promotion(leadership_token)
        # elector 放锁注册为最早的 push → 退出时最后执行（先 drain 后放锁）
        resources.push_async_callback(elector.release)

        dispatcher = RunDispatcher(
            bus=run_bus,
            token_provider=lambda: elector.token,
            scan_interval_seconds=DistributedRunsConfig.queued_scan_interval_seconds,
        )
        # dispatcher 停止排在 run_manager drain 之后、elector 放锁之前
        resources.push_async_callback(dispatcher.stop)

        # 后台监控 advisory lock 连接存活：失锁即失效 token 并停掉所有 live Run
        async def _monitor_owner_lock():
            await pg_manager.monitor_advisory_lock()
            if pg_manager.advisory_lock_ready is False:
                logger.error("owner lock 已丢失，停止所有 live Run 并进入 not-ready")
                elector.invalidate()
                await run_manager.shutdown(drain_seconds=0)

        lock_monitor = asyncio.create_task(
            _monitor_owner_lock(), name="advisory-lock-monitor"
        )
        async def _cancel_lock_monitor():
            if not lock_monitor.done():
                lock_monitor.cancel()
                try:
                    await lock_monitor
                except asyncio.CancelledError:
                    pass
        resources.push_async_callback(_cancel_lock_monitor)

        resources.push_async_callback(shutdown_sandboxes)
        await init_checkpointer()
        resources.push_async_callback(close_checkpointer)
        await init_knowledge_base()
        resources.push_async_callback(close_knowledge_base)
        resources.push_async_callback(
            run_manager.shutdown,
            drain_seconds=StreamConfig.run_shutdown_drain_seconds,
        )
        # 进程退出时取消运行中任务并停掉隔离 loop
        resources.callback(shutdown_bg_subagents)

        async def _start_leader_runtime() -> None:
            """leader 专属 singleton：dispatcher / 调度器 / 信令通道 / 记忆任务。

            后台任务执行面（executor/隔离循环）与 shutdown_bg_subagents 也仅
            leader 需要——follower 没有注册表可停。
            """
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

        await sync_existing_kb_collection_configs()
        # ---- leader-only singleton runtime（task 2.3）：follower 不运行 ----
        if elector.is_leader:
            await _start_leader_runtime()
            role = "execution leader"
        else:
            role = "Web worker (follower)"
            logger.info(
                "本进程为 Web worker：不运行 Agent producer / 调度器 / 信令通道 / 记忆任务"
            )

        logger.info(f'🚀 {AppConfig.app_name}启动成功 role={role}')
        yield


app = FastAPI(
    title=AppConfig.app_name,
    description=f'{AppConfig.app_name}接口文档',
    version=AppConfig.app_version,
    lifespan=lifespan,
)

handle_exception(app)
app.add_middleware(RequestLogContextMiddleware)
app.add_middleware(CsrfMiddleware)

# 加载路由列表
controller_list = [
    {'router': auth_router, 'tags': ['认证模块']},
    {'router':  user_router, 'tags': ['用户模块']},
    {'router':  user_settings_router, 'tags': ['用户设置']},
    {'router':  user_llm_router, 'tags': ['用户模型']},
    {'router':  settings_router, 'tags': ['设置控制面']},
    {'router':  chat_router, 'tags': ['聊天历史模块']},
    {'router':  knowledge_base_router, 'tags': ['知识库模块']},
    {'router':  skill_router, 'tags': ['Skill 模块']},
    {'router':  chat_attachment_router, 'tags': ['聊天附件模块']},
    {'router':  model_router, 'tags': ['模型模块']},
    {'router':  mcp_router, 'tags': ['MCP 模块']},
]

for controller in controller_list:
    app.include_router(router=controller.get('router'), tags=controller.get('tags'))


@app.get('/health', tags=['系统'])
async def health_check():
    """健康检查端点（task 6.1）：角色 / adapter / Redis 依赖状态显式上报。

    liveness 与 readiness 不分离（本端点即 readiness）：Redis degraded 时
    仍返回 200——Web 面可路由、仅创建 Run 的调用方按 ``redis_reachable``
    自行拒绝，已有 Run 的查询/stop/HITL 不因降级不可用。
    """
    role = "follower" if DistributedRunsConfig.backend == "redis" else "leader"
    redis_reachable = None  # memory 模式不探测
    if DistributedRunsConfig.backend == "redis":
        try:
            from noesis.services.run_service import run_bus

            client = getattr(run_bus, "_client", None)
            redis_reachable = bool(await asyncio.wait_for(client.ping(), timeout=2)) if client else False
        except Exception:  # noqa: BLE001
            redis_reachable = False
    body = {
        "status": "healthy",
        "app": AppConfig.app_name,
        "run_bus_backend": DistributedRunsConfig.backend,
        "multi_worker_supported": DistributedRunsConfig.backend == "redis",
        "leader_role": role,
        "execution_lock_ready": pg_manager.advisory_lock_ready is not False,
        "redis_reachable": redis_reachable,
    }
    if pg_manager.advisory_lock_ready is False:
        return JSONResponse(status_code=503, content={**body, "status": "not-ready"})
    if redis_reachable is False:
        body["status"] = "degraded"
    return body
