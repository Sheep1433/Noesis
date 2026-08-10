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

    try:
        from noesis.services.kb_collection_config_service import KbCollectionConfigService
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            await KbCollectionConfigService.ensure_defaults_for_qdrant_collections(db)
            await db.commit()
    except Exception as exc:
        logger.warning(f"[KB Init] PostgreSQL 集合配置回填失败: {exc}")
        return

    logger.info("[KB Init] 已有知识库集合配置同步完成")
