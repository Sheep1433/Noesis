import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from server.exception_handlers import handle_exception
from server.middleware.csrf import CsrfMiddleware
from noesis.config.env import AppConfig, DistributedRunsConfig, StreamConfig
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
from noesis.agents.subagents import (
    shutdown as shutdown_bg_subagents,
)
from noesis.runtime.main_loop import capture_main_loop
from server.wiring import wire_runtime_observability
from noesis.services.scheduled_task_scheduler import (
    start_scheduled_task_scheduler,
    stop_scheduled_task_scheduler,
)
from noesis.services.channels.telegram_runtime import start_telegram_runtime, stop_telegram_runtime
from noesis.services.memory.consolidation import (
    start_memory_consolidator,
    stop_memory_consolidator,
)
from noesis.services.memory.extraction import start_memory_sweeper, stop_memory_sweeper
from noesis.services.channels.feishu_runtime import start_feishu_runtime, stop_feishu_runtime
from server.bootstrap.kb import sync_existing_kb_collection_configs
from noesis.services.run_recovery_service import RunRecoveryService
from noesis.services.run_service import run_manager, run_bus
from noesis.services.leader_elector import LeaderElector
from noesis.services.run_dispatcher import RunDispatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
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

        # ---- Leader elector：竞争执行锁（key 不变，滚动升级期新旧互斥）并提交
        # 全局 leadership term。P1 为单进程 memory 模式：第二实例获取失败 fail-fast。
        elector = LeaderElector(cluster_id=DistributedRunsConfig.cluster_id)
        leadership_token = await elector.acquire()
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

        # ---- leader-only：recovery 跳过未 claim 的 queued Run（dispatcher 补扫启动）
        async with pg_manager.get_async_session_context() as recovery_db:
            await RunRecoveryService.recover_orphaned_runs(
                recovery_db, current_leader_term=leadership_token.term
            )
            from noesis.services.subagent_session_service import SubagentSessionService

            orphaned_subagents = await SubagentSessionService.reconcile_orphaned_runs(recovery_db)
            if orphaned_subagents:
                logger.warning("子 Agent 对账：{} 个遗留 run 已标记为中断", orphaned_subagents)

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

        await sync_existing_kb_collection_configs()
        # ---- leader-only singleton runtime ----
        await dispatcher.start()
        start_scheduled_task_scheduler()
        resources.push_async_callback(stop_scheduled_task_scheduler)
        start_telegram_runtime()
        resources.push_async_callback(stop_telegram_runtime)
        start_feishu_runtime()
        resources.push_async_callback(stop_feishu_runtime)
        await start_memory_sweeper()
        resources.push_async_callback(stop_memory_sweeper)
        await start_memory_consolidator()
        resources.push_async_callback(stop_memory_consolidator)

        logger.info(f'🚀 {AppConfig.app_name}启动成功')
        yield


app = FastAPI(
    title=AppConfig.app_name,
    description=f'{AppConfig.app_name}接口文档',
    version=AppConfig.app_version,
    lifespan=lifespan,
)

handle_exception(app)
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
    """健康检查端点"""
    if pg_manager.advisory_lock_ready is False:
        return JSONResponse(
            status_code=503,
            content={"status": "not-ready", "app": AppConfig.app_name},
        )
    return {'status': 'healthy', 'app': AppConfig.app_name}
