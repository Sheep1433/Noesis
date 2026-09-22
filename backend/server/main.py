import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from server.exception_handlers import handle_exception
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
from server.bootstrap.kb import sync_existing_kb_collection_configs
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

        # ---- Leader elector：竞争执行锁（key 不变，滚动升级期新旧互斥）并提交
        # 全局 leadership term。memory 模式第二实例 fail-fast；redis 模式未获锁
        # 的进程以 Web worker 待命 + 周期重竞选，晋升回调承载 leader 面装配
        # （task 2.3/2.4）。
        elector = LeaderElector(cluster_id=DistributedRunsConfig.cluster_id)
        from server.bootstrap.leader_runtime import build_promotion_callback

        _on_promotion = build_promotion_callback(
            elector=elector,
            run_bus=run_bus,
            run_manager=run_manager,
            resources=resources,
            leader_components=leader_components,
        )
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

        from server.bootstrap.leader_runtime import start_leader_singletons

        await sync_existing_kb_collection_configs()
        # ---- leader-only singleton runtime（task 2.3）：follower 不运行 ----
        if elector.is_leader:
            await start_leader_singletons(
                dispatcher=dispatcher, resources=resources
            )
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

# 写请求 CSRF：路由器级依赖统一挂载（单一实现）。auth_router 除外——
# 登录/注册需豁免（可能携带旧 session cookie 而无法提供新 token），
# 其 logout / logout-all 端点各自显式声明 require_csrf。
from fastapi import Depends

from server.auth_dependencies import require_csrf as _require_csrf

for controller in controller_list:
    router = controller.get('router')
    csrf_deps = [] if router is auth_router else [Depends(_require_csrf)]
    app.include_router(
        router=router,
        tags=controller.get('tags'),
        dependencies=csrf_deps,
    )


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
