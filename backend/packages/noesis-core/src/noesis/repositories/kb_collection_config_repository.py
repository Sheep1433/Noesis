"""Knowledge-base collection-config repository.

Reads and persists ``kb_collection_config`` rows via ``pg_manager``. Replaces
the DB methods of ``KbCollectionConfigService`` so the core KB
engine and tools can read collection config without dependency injection.

Constructed with an async session (request-scoped, shared transaction) for
async paths; the synchronous ``load_query_params_sync`` uses
``pg_manager.get_sync_session()`` for Agent-tool thread-internal reads.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.knowledge.chunking.params import (
    deep_merge_mapping,
    normalize_collection_processing_params,
    normalize_collection_query_params,
)
from noesis.runtime.logging import logger
from noesis.storage.postgres.manager import pg_manager
from noesis.storage.postgres.models.knowledge import TKbCollectionConfig


class KbCollectionConfigRepository:
    """集合级 processing_params / query_params 持久化。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def platform_processing_defaults() -> Dict[str, Any]:
        return normalize_collection_processing_params({})

    @staticmethod
    def platform_query_defaults() -> Dict[str, Any]:
        return normalize_collection_query_params({})

    async def get_row(self, collection_name: str) -> Optional[TKbCollectionConfig]:
        name = (collection_name or "").strip()
        if not name:
            return None
        result = await self.db.execute(
            select(TKbCollectionConfig).where(TKbCollectionConfig.collection_name == name)
        )
        return result.scalar_one_or_none()

    async def get_config(self, collection_name: str) -> Optional[Dict[str, Any]]:
        row = await self.get_row(collection_name)
        if row is None:
            return None
        return {
            "collection_name": row.collection_name,
            "processing_params": normalize_collection_processing_params(row.processing_params),
            "query_params": normalize_collection_query_params(row.query_params),
        }

    async def create_default(self, collection_name: str) -> TKbCollectionConfig:
        name = (collection_name or "").strip()
        existing = await self.get_row(name)
        if existing is not None:
            return existing
        row = TKbCollectionConfig(
            collection_name=name,
            processing_params=self.platform_processing_defaults(),
            query_params=self.platform_query_defaults(),
        )
        self.db.add(row)
        await self.db.flush()
        logger.info(f"[KbCollectionConfig] 创建默认配置: {name}")
        return row

    async def delete_config(self, collection_name: str) -> bool:
        row = await self.get_row(collection_name)
        if row is None:
            return False
        await self.db.delete(row)
        await self.db.flush()
        logger.info(f"[KbCollectionConfig] 删除配置: {collection_name}")
        return True

    async def patch_config(
        self,
        collection_name: str,
        *,
        processing_params: Optional[Mapping[str, Any]] = None,
        query_params: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        row = await self.get_row(collection_name)
        if row is None:
            return None
        if processing_params is not None:
            current = normalize_collection_processing_params(row.processing_params)
            row.processing_params = normalize_collection_processing_params(
                deep_merge_mapping(current, processing_params)
            )
        if query_params is not None:
            current_q = normalize_collection_query_params(row.query_params)
            row.query_params = normalize_collection_query_params(
                deep_merge_mapping(current_q, query_params)
            )
        await self.db.flush()
        return await self.get_config(collection_name)


def load_query_params_sync(collection_name: str) -> Dict[str, Any]:
    """Agent 同步上下文读取集合 query_params；失败时回退平台默认。

    经 ``pg_manager.get_sync_session()`` 读，不另建 sync engine。
    """
    name = (collection_name or "").strip()
    try:
        with pg_manager.get_sync_session() as db:
            row = db.execute(
                select(TKbCollectionConfig).where(TKbCollectionConfig.collection_name == name)
            ).scalar_one_or_none()
            if row is None:
                return normalize_collection_query_params({})
            return normalize_collection_query_params(row.query_params)
    except Exception as exc:
        logger.warning(
            f"[KbCollectionConfig] 同步读取 query_params 失败 collection={collection_name}: {exc}"
        )
        return normalize_collection_query_params({})
