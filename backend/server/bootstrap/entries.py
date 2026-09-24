"""三入口装配（worker-role-split Phase 3）：web / control / worker。

进程角色三分，无过渡态（单进程全干入口退役）：

- **web × N**（无状态）：全部业务路由、认证、CSRF、SSE 转发（远端 run
  经 bus hub 订阅 + DB 快照）；
- **control × 1**（advisory lock 防双开）：定时调度器、Telegram/飞书
  通道、记忆任务、control 对账（主 run 阶段化 + 定时任务）；
- **worker × N**：run 认领执行（dispatcher + claim epoch fencing + 心跳）、
  命令消费（shard 分片）、checkpointer、KB、沙箱与子代理执行面、
  worker 对账（executor 热集 + 命令重置 + 通知装载）。

强制 ``NOESIS_RUN_BUS_BACKEND=redis``：三进程形态下事件与唤醒都跨进程，
memory 模式（进程内总线）无法工作，启动 fail-fast。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from noesis.config.env import (
    AppConfig,
    DistributedRunsConfig,
)
from noesis.runtime.logging import logger


def _require_redis_bus() -> None:
    """三入口形态强制 redis bus（事件/唤醒跨进程），memory 模式 fail-fast。"""
    if DistributedRunsConfig.backend != "redis":
        raise ValueError(
            "三入口形态（web/control/worker）要求 NOESIS_RUN_BUS_BACKEND=redis"
            "（事件与唤醒需跨进程；memory 进程内总线仅单进程部署合法）。"
            "最小配置：backend/.env 设 NOESIS_RUN_BUS_BACKEND=redis、"
            "REDIS_URL=redis://localhost:6379、NOESIS_CLUSTER_ID=<集群名>，"
            "并确保 Redis 在跑（scripts/run.sh dev 会随栈拉起）。"
        )


def process_instance_id(role: str) -> str:
    """进程实例 ID：role + pid，同机多进程唯一。"""
    return f"{AppConfig.app_name}-{role}-{os.getpid()}"


async def _base_resources(resources: AsyncExitStack) -> None:
    """三角色共用的启动底座：端口注册、观测、数据库与迁移。"""
    from noesis.services.runtime_ports import register_runtime_ports
    from noesis.runtime.main_loop import capture_main_loop
    from server.langfuse import sync_langfuse_env_from_app_config
    from server.wiring import wire_runtime_observability
    from noesis.storage.postgres.manager import pg_manager
    from server.db import init_database

    register_runtime_ports()
    capture_main_loop()
    sync_langfuse_env_from_app_config()
    wire_runtime_observability()
    resources.push_async_callback(pg_manager.close)
    # migration lock（独立 key）：多进程并发启动时串行跑 migration
    await pg_manager.acquire_migration_lock()
    try:
        await init_database()
    finally:
        await pg_manager.release_migration_lock()


def _add_health_route(app: FastAPI, role: str) -> None:
    """挂 /health 角色上报端点（control / worker 最小 app 与 all-in-one 共用）。"""

    @app.get("/health", tags=["系统"])
    async def health_check():
        from noesis.services.run_service import run_bus
        from noesis.storage.postgres.manager import pg_manager

        body = {
            "status": "healthy",
            "app": AppConfig.app_name,
            "role": role,
            "run_bus_backend": DistributedRunsConfig.backend,
            "execution_lock_ready": pg_manager.advisory_lock_ready is not False,
        }
        client = getattr(run_bus, "_client", None)
        if client is not None:
            try:
                body["redis_reachable"] = bool(
                    await asyncio.wait_for(client.ping(), timeout=2)
                )
            except Exception:  # noqa: BLE001
                body["redis_reachable"] = False
        if pg_manager.advisory_lock_ready is False:
            return JSONResponse(status_code=503, content={**body, "status": "not-ready"})
        return body


def _health_app(role: str) -> FastAPI:
    """control / worker 的最小 app：只有 /health（角色显式上报）。"""
    app = FastAPI(title=f"{AppConfig.app_name}-{role}")
    _add_health_route(app, role)
    return app


def _mount_web_routes(app: FastAPI) -> None:
    """web 面路由挂载（build_web_app 与 build_all_in_one_app 共用）。"""
    from fastapi import Depends
    from server.api import (
        auth_router,
        chat_attachment_router,
        chat_router,
        knowledge_base_router,
        mcp_router,
        model_router,
        settings_router,
        skill_router,
        user_llm_router,
        user_router,
        user_settings_router,
    )
    from server.auth_dependencies import require_csrf as _require_csrf
    from server.middleware.request_log_context import RequestLogContextMiddleware
    from server.exception_handlers import handle_exception

    handle_exception(app)
    app.add_middleware(RequestLogContextMiddleware)
    # 路由器级 CSRF（单一实现，挂载守卫契约测试钉住）；auth_router 豁免
    # 由其端点自声明 require_csrf 覆盖
    for router in (
        auth_router,
        user_router,
        user_settings_router,
        user_llm_router,
        settings_router,
        chat_router,
        knowledge_base_router,
        skill_router,
        chat_attachment_router,
        model_router,
        mcp_router,
    ):
        csrf_deps = [] if router is auth_router else [Depends(_require_csrf)]
        app.include_router(router=router, dependencies=csrf_deps)


# ---------------------------------------------------------------------------
# web：全部业务路由 + SSE 转发（无执行面）
# ---------------------------------------------------------------------------


def build_web_app() -> FastAPI:
    """web × N：全部业务路由 + SSE 转发（无执行面，三入口形态，redis 强制）。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _require_redis_bus()
        from noesis.knowledge.runtime import close_knowledge_base, init_knowledge_base

        async with AsyncExitStack() as resources:
            await _base_resources(resources)
            await init_knowledge_base()
            resources.push_async_callback(close_knowledge_base)
            logger.info(f"🚀 {AppConfig.app_name} web 启动成功（无执行面）")
            yield

    app = FastAPI(
        title=AppConfig.app_name,
        description=f"{AppConfig.app_name}接口文档",
        version=AppConfig.app_version,
    )
    _mount_web_routes(app)
    _add_health_route(app, "web")
    app.router.lifespan_context = lifespan
    return app


# ---------------------------------------------------------------------------
# control：调度器 / 通道 / 记忆任务 / 对账（advisory lock 防双开）
# ---------------------------------------------------------------------------


def build_control_app() -> FastAPI:
    """control × 1（advisory lock 防双开）：调度器 / 通道 / 记忆 / 对账 + /health。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _require_redis_bus()
        from noesis.storage.postgres.manager import pg_manager
        from server.bootstrap.leader_runtime import (
            CONTROL_RECONCILE_ORDER,
            _control_reconcile_steps,
            run_reconcile_group,
            start_control_singletons,
        )

        async with AsyncExitStack() as resources:
            await _base_resources(resources)
            # advisory lock 防双开：第二个 control 直接抛错退出（单控制面不变量）
            await pg_manager.acquire_advisory_lock()
            resources.push_async_callback(pg_manager.release_advisory_lock)
            async with pg_manager.get_async_session_context() as recovery_db:
                await run_reconcile_group(_control_reconcile_steps(recovery_db))
            logger.info("control 对账完成 steps={}", CONTROL_RECONCILE_ORDER)
            await start_control_singletons(resources=resources)
            logger.info(f"🚀 {AppConfig.app_name} control 启动成功（调度/通道/记忆）")
            yield

    app = _health_app("control")
    app.router.lifespan_context = lifespan
    return app


# ---------------------------------------------------------------------------
# worker：认领执行面（dispatcher / 命令消费 / checkpointer / KB / 沙箱 / 子代理）
# ---------------------------------------------------------------------------


def build_worker_app() -> FastAPI:
    """worker × N：认领执行面（lifespan 装配）+ /health。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _require_redis_bus()
        from noesis.agents.background import shutdown as shutdown_bg_subagents
        from noesis.agents.backends.sandbox_lifecycle import shutdown_sandboxes
        from noesis.config.checkpointer import close_checkpointer, init_checkpointer
        from noesis.config.env import StreamConfig
        from noesis.knowledge.runtime import close_knowledge_base, init_knowledge_base
        from noesis.services.run_command_service import RunCommandConsumer
        from noesis.services.run_dispatcher import RunDispatcher
        from noesis.services.run_service import run_bus, run_manager
        from noesis.storage.postgres.manager import pg_manager
        from server.bootstrap.leader_runtime import (
            WORKER_RECONCILE_ORDER,
            _worker_reconcile_steps,
            run_reconcile_group,
        )

        instance_id = process_instance_id("worker")
        async with AsyncExitStack() as resources:
            await _base_resources(resources)
            await init_checkpointer()
            resources.push_async_callback(close_checkpointer)
            await init_knowledge_base()
            resources.push_async_callback(close_knowledge_base)

            # 事件发布面：worker 的 run 事件经 bus 广播给 web 进程扇出
            run_manager.attach_bus(run_bus)

            # worker 对账（顺序约束：claimed 重置 → 命令消费；queued 重建 → dispatcher）
            async with pg_manager.get_async_session_context() as recovery_db:
                await run_reconcile_group(_worker_reconcile_steps(recovery_db))
            logger.info("worker 对账完成 steps={}", WORKER_RECONCILE_ORDER)

            # 命令消费（分片：只认领本进程持有目标的命令）
            def _shard_filter(row) -> bool:
                run_id = getattr(row, "run_id", None)
                if run_id:
                    try:
                        run_manager.get(run_id)
                        return True
                    except KeyError:
                        return False
                # 无 run 归属的命令（bg_task_*）按任务热集判定
                task_id = getattr(row, "task_id", None)
                if task_id:
                    from noesis.agents.background.executor import (
                        BackgroundTaskExecutor,
                    )

                    return BackgroundTaskExecutor.get(task_id) is not None
                return False

            consumer = RunCommandConsumer(
                bus=run_bus,
                scan_interval_seconds=DistributedRunsConfig.command_scan_interval_seconds,
                retention_days=DistributedRunsConfig.command_retention_days,
                shard_filter=_shard_filter,
            )
            await consumer.start()
            resources.push_async_callback(consumer.stop)

            dispatcher = RunDispatcher(
                bus=run_bus,
                instance_id=instance_id,
                scan_interval_seconds=DistributedRunsConfig.queued_scan_interval_seconds,
            )
            await dispatcher.start()
            # dispatcher 停止排在 run_manager drain 之后
            resources.push_async_callback(dispatcher.stop)
            resources.push_async_callback(shutdown_sandboxes)
            resources.push_async_callback(
                run_manager.shutdown,
                drain_seconds=StreamConfig.run_shutdown_drain_seconds,
            )
            # 退出时取消运行中子任务并停掉隔离 loop
            resources.callback(shutdown_bg_subagents)
            logger.info(
                f"🚀 {AppConfig.app_name} worker 启动成功 instance_id={instance_id}"
            )
            yield

    app = _health_app("worker")
    app.router.lifespan_context = lifespan
    return app


# ---------------------------------------------------------------------------
# all-in-one：单进程全干（本地自用默认形态；memory/redis 总线均可）
# ---------------------------------------------------------------------------


def build_all_in_one_app() -> FastAPI:
    """单进程全干（本地自用形态）：web 路由 + control + worker 一体。

    与三入口共用全部装配件；唯一区别是不做 redis 强制——按配置用
    memory（零额外依赖，进程内总线）或 redis（为未来拆分预热）。
    advisory lock 仍获取：防「手滑起两个 all-in-one」的双执行。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from noesis.agents.background import shutdown as shutdown_bg_subagents
        from noesis.agents.backends.sandbox_lifecycle import shutdown_sandboxes
        from noesis.config.checkpointer import close_checkpointer, init_checkpointer
        from noesis.config.env import StreamConfig
        from noesis.knowledge.runtime import close_knowledge_base, init_knowledge_base
        from noesis.services.leader_elector import LeaderElector
        from noesis.services.run_command_service import RunCommandConsumer
        from noesis.services.run_dispatcher import RunDispatcher
        from noesis.services.run_service import run_bus, run_manager
        from noesis.storage.postgres.manager import pg_manager
        from server.bootstrap.leader_runtime import (
            CONTROL_RECONCILE_ORDER,
            WORKER_RECONCILE_ORDER,
            _control_reconcile_steps,
            _worker_reconcile_steps,
            run_reconcile_group,
            start_control_singletons,
        )

        instance_id = process_instance_id("all-in-one")
        async with AsyncExitStack() as resources:
            await _base_resources(resources)
            # 防双开：单进程形态下第二个实例同样 fail-fast（memory 总线
            # 无跨进程感知，双写不可检测，只能靠锁预防）
            elector = LeaderElector(cluster_id=DistributedRunsConfig.cluster_id)
            await elector.acquire()
            resources.push_async_callback(elector.release)

            await init_checkpointer()
            resources.push_async_callback(close_checkpointer)
            await init_knowledge_base()
            resources.push_async_callback(close_knowledge_base)
            run_manager.attach_bus(run_bus)

            # 对账：control 组（主 run 阶段化 + 定时任务）+ worker 组
            # （executor 热集 + 命令重置 + 通知装载），顺序同三入口
            async with pg_manager.get_async_session_context() as recovery_db:
                await run_reconcile_group(_control_reconcile_steps(recovery_db))
                await run_reconcile_group(_worker_reconcile_steps(recovery_db))
            logger.info(
                "all-in-one 对账完成 control={} worker={}",
                CONTROL_RECONCILE_ORDER, WORKER_RECONCILE_ORDER,
            )

            await start_control_singletons(resources=resources)

            # 命令消费：单进程持有全部 run，不过滤
            consumer = RunCommandConsumer(
                bus=run_bus,
                scan_interval_seconds=DistributedRunsConfig.command_scan_interval_seconds,
                retention_days=DistributedRunsConfig.command_retention_days,
            )
            await consumer.start()
            resources.push_async_callback(consumer.stop)

            dispatcher = RunDispatcher(
                bus=run_bus,
                instance_id=instance_id,
                scan_interval_seconds=DistributedRunsConfig.queued_scan_interval_seconds,
            )
            await dispatcher.start()
            resources.push_async_callback(dispatcher.stop)
            resources.push_async_callback(shutdown_sandboxes)
            resources.push_async_callback(
                run_manager.shutdown,
                drain_seconds=StreamConfig.run_shutdown_drain_seconds,
            )
            resources.callback(shutdown_bg_subagents)
            logger.info(
                f"🚀 {AppConfig.app_name} all-in-one 启动成功 "
                f"instance_id={instance_id} bus={DistributedRunsConfig.backend}"
            )
            yield

    app = FastAPI(
        title=AppConfig.app_name,
        description=f"{AppConfig.app_name}接口文档",
        version=AppConfig.app_version,
    )
    _mount_web_routes(app)
    _add_health_route(app, "all-in-one")
    app.router.lifespan_context = lifespan
    return app


__all__ = [
    "build_web_app",
    "build_control_app",
    "build_worker_app",
    "build_all_in_one_app",
    "process_instance_id",
]
