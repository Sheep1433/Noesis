from contextlib import asynccontextmanager
from fastapi import FastAPI

from noesis_server.exceptions.handle import handle_exception
from noesis_server.middleware.csrf import CsrfMiddleware
from noesis.config.env import AppConfig
from noesis.config.checkpointer import close_checkpointer, init_checkpointer
from noesis_server.infrastructure.database.dependency import init_database
from noesis_server.infrastructure.database.engine import async_engine
from noesis.runtime.logging import logger
from noesis_server.infrastructure.observability.langfuse import sync_langfuse_env_from_app_config
from noesis_server.api import (
    user_router,
    chat_router,
    knowledge_base_router,
    skill_router,
    chat_attachment_router,
    model_router,
    auth_router,
    mcp_router,
    user_settings_router,
)
from noesis_server.kb.qdrant import init_qdrant_client, close_qdrant_client
from noesis.backends.sandbox_lifecycle import shutdown_sandboxes
from noesis_server.services.harness_wiring import wire_harness_platform_deps
from noesis_server.services.scheduled_task_scheduler import (
    start_scheduled_task_scheduler,
    stop_scheduled_task_scheduler,
)
from noesis_server.services.channels.telegram_runtime import start_telegram_runtime, stop_telegram_runtime
from noesis_server.bootstrap.kb import ensure_default_kb_collections


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f'⏰️ {AppConfig.app_name}开始启动')
    sync_langfuse_env_from_app_config()
    wire_harness_platform_deps()
    await init_database()
    await init_checkpointer()
    # 初始化 Qdrant 连接
    await init_qdrant_client()
    await ensure_default_kb_collections()
    start_scheduled_task_scheduler()
    start_telegram_runtime()
    logger.info(f'🚀 {AppConfig.app_name}启动成功')
    yield
    await stop_telegram_runtime()
    await stop_scheduled_task_scheduler()
    # 关闭 Qdrant 连接
    await close_qdrant_client()
    await close_checkpointer()
    await shutdown_sandboxes()
    # 关闭数据库连接池（等待现有连接完成，避免 CancelledError）
    logger.info("正在关闭数据库连接池...")
    await async_engine.dispose()
    logger.info("数据库连接池已关闭")


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
