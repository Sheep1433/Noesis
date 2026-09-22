"""知识库启动同步：为 Qdrant 已有集合补全 PostgreSQL 配置。"""
from __future__ import annotations

from noesis.knowledge.runtime import knowledge_base
from noesis.runtime.logging import logger

async def sync_existing_kb_collection_configs() -> None:
    """为 Qdrant 已有集合补全 PostgreSQL 配置，不创建新集合。"""
    if not knowledge_base.connected:
        logger.warning("[KB Init] Qdrant 未连接，跳过集合配置同步")
        return

    service = knowledge_base.service()
    if not service.client:
        logger.warning("[KB Init] Qdrant 客户端不可用，跳过集合配置同步")
        return

    # 预期失败面：存储连接/查询异常（SQLAlchemy）与 Qdrant 知识库异常
    # （集合列举在 service 内发生）。其余异常（代码 bug）不上吞——启动期
    # 大声失败比静默半初始化更可排障。
    from sqlalchemy.exc import SQLAlchemyError

    from noesis.knowledge.base import KnowledgeBaseException
    from noesis.services.kb_collection_config_service import KbCollectionConfigService
    from noesis.storage.postgres.manager import pg_manager

    try:
        async with pg_manager.get_async_session_context() as db:
            await KbCollectionConfigService.ensure_defaults_for_qdrant_collections(db)
            await db.commit()
    except (SQLAlchemyError, KnowledgeBaseException) as exc:
        logger.opt(exception=True).warning(
            "[KB Init] PostgreSQL 集合配置回填失败（连接/存储层，跳过）: {}", exc
        )
        return

    logger.info("[KB Init] 已有知识库集合配置同步完成")
