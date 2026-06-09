from contextlib import asynccontextmanager
from fastapi import FastAPI

from exceptions.handle import handle_exception
from config.env import AppConfig
from config.get_db import init_create_table
from config.database import async_engine
from utils.log_util import logger
from utils.langfuse_tracing import sync_langfuse_env_from_app_config
from api import (
    login_router,
    user_router,
    chat_router,
    knowledge_base_router,
    skill_router,
    chat_attachment_router,
)
from services.qdrant_service import init_qdrant_client, close_qdrant_client
from kb.seed_collections import ensure_default_kb_collections


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f'⏰️ {AppConfig.app_name}开始启动')
    sync_langfuse_env_from_app_config()
    await init_create_table()
    # 初始化 Qdrant 连接
    await init_qdrant_client()
    await ensure_default_kb_collections()
    logger.info(f'🚀 {AppConfig.app_name}启动成功')
    yield
    # 关闭 Qdrant 连接
    await close_qdrant_client()
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

# 加载路由列表
controller_list = [
    {'router': login_router, 'tags': ['登录模块']},
    {'router':  user_router, 'tags': ['用户模块']},
    {'router':  chat_router, 'tags': ['聊天历史模块']},
    {'router':  knowledge_base_router, 'tags': ['知识库模块']},
    {'router':  skill_router, 'tags': ['Skill 模块']},
    {'router':  chat_attachment_router, 'tags': ['聊天附件模块']},
]

for controller in controller_list:
    app.include_router(router=controller.get('router'), tags=controller.get('tags'))


@app.get('/health', tags=['系统'])
async def health_check():
    """健康检查端点"""
    return {'status': 'healthy', 'app': AppConfig.app_name}

