from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI

from server.exception_handlers import handle_exception
from server.middleware.csrf import CsrfMiddleware
from noesis.config.env import AppConfig, StreamConfig
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
    settings_router,
)
from noesis.knowledge.runtime import init_knowledge_base, close_knowledge_base
from noesis.agents.backends.sandbox_lifecycle import shutdown_sandboxes
from server.wiring import wire_runtime_observability
from noesis.services.scheduled_task_scheduler import (
    start_scheduled_task_scheduler,
    stop_scheduled_task_scheduler,
)
from noesis.services.memory_dream_scheduler import start_memory_dream_scheduler, stop_memory_dream_scheduler
from noesis.services.channels.telegram_runtime import start_telegram_runtime, stop_telegram_runtime
from noesis.services.channels.feishu_runtime import start_feishu_runtime, stop_feishu_runtime
from server.bootstrap.kb import sync_existing_kb_collection_configs
from noesis.services.run_recovery_service import RunRecoveryService
from noesis.services.run_service import run_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f'⏰️ {AppConfig.app_name}开始启动')
    sync_langfuse_env_from_app_config()
    wire_runtime_observability()
    async with AsyncExitStack() as resources:
        resources.push_async_callback(pg_manager.close)
        await init_database()
        async with pg_manager.get_async_session_context() as recovery_db:
            await RunRecoveryService.recover_orphaned_runs(recovery_db)

        resources.push_async_callback(shutdown_sandboxes)
        await init_checkpointer()
        resources.push_async_callback(close_checkpointer)
        await init_knowledge_base()
        resources.push_async_callback(close_knowledge_base)
        resources.push_async_callback(
            run_manager.shutdown,
            drain_seconds=StreamConfig.run_shutdown_drain_seconds,
        )

        await sync_existing_kb_collection_configs()
        start_scheduled_task_scheduler()
        resources.push_async_callback(stop_scheduled_task_scheduler)
        start_memory_dream_scheduler()
        resources.push_async_callback(stop_memory_dream_scheduler)
        start_telegram_runtime()
        resources.push_async_callback(stop_telegram_runtime)
        start_feishu_runtime()
        resources.push_async_callback(stop_feishu_runtime)

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
    return {'status': 'healthy', 'app': AppConfig.app_name}
