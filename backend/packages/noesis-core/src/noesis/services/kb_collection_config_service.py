"""知识库集合配置服务（薄编排，委托 noesis.repositories）。

DB 方法委托 ``KbCollectionConfigRepository``；本类负责集合默认配置的应用编排。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.knowledge.chunking.params import (
    normalize_collection_processing_params,
    normalize_collection_query_params,
)
from noesis.repositories.kb_collection_config_repository import (
    KbCollectionConfigRepository,
    load_query_params_sync,
)


class KbCollectionConfigService:
    @classmethod
    def platform_processing_defaults(cls) -> Dict[str, Any]:
        return KbCollectionConfigRepository.platform_processing_defaults()

    @classmethod
    def platform_query_defaults(cls) -> Dict[str, Any]:
        return KbCollectionConfigRepository.platform_query_defaults()

    @classmethod
    def _repo(cls, db: AsyncSession) -> KbCollectionConfigRepository:
        return KbCollectionConfigRepository(db)

    @classmethod
    async def get_row(cls, db: AsyncSession, collection_name: str):
        return await cls._repo(db).get_row(collection_name)

    @classmethod
    async def get_config(cls, db: AsyncSession, collection_name: str) -> Optional[Dict[str, Any]]:
        return await cls._repo(db).get_config(collection_name)

    @classmethod
    async def create_default(cls, db: AsyncSession, collection_name: str):
        return await cls._repo(db).create_default(collection_name)

    @classmethod
    async def delete_config(cls, db: AsyncSession, collection_name: str) -> bool:
        return await cls._repo(db).delete_config(collection_name)

    @classmethod
    async def patch_config(
        cls,
        db: AsyncSession,
        collection_name: str,
        *,
        processing_params: Optional[Mapping[str, Any]] = None,
        query_params: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return await cls._repo(db).patch_config(
            collection_name,
            processing_params=processing_params,
            query_params=query_params,
        )

    @classmethod
    async def ensure_defaults_for_qdrant_collections(cls, db: AsyncSession) -> int:
        """为 Qdrant 已有但关系库缺失的 collection 回填默认配置行。"""
        from noesis.knowledge.runtime import knowledge_base

        if not knowledge_base.connected:
            return 0

        service = knowledge_base.service()
        repo = cls._repo(db)
        created = 0
        for col in service.get_collections():
            name = (col.get("name") or "").strip()
            if not name:
                continue
            row = await repo.get_row(name)
            if row is None:
                await repo.create_default(name)
                created += 1
        return created

    @classmethod
    def load_query_params_sync(cls, collection_name: str) -> Dict[str, Any]:
        """Agent 同步上下文读取集合 query_params。"""
        return load_query_params_sync(collection_name)

    @classmethod
    def load_processing_params_sync(cls, collection_name: str) -> Dict[str, Any]:
        """Agent 同步上下文读取集合 processing_params。"""
        from noesis.repositories.kb_collection_config_repository import load_query_params_sync as _load
        # 复用 sync session 路径读 processing_params
        from noesis.storage.postgres.manager import pg_manager
        from noesis.storage.postgres.models.knowledge import TKbCollectionConfig
        from sqlalchemy import select

        name = (collection_name or "").strip()
        try:
            with pg_manager.get_sync_session() as db:
                row = db.execute(
                    select(TKbCollectionConfig).where(TKbCollectionConfig.collection_name == name)
                ).scalar_one_or_none()
                if row is None:
                    return normalize_collection_processing_params({})
                return normalize_collection_processing_params(row.processing_params)
        except Exception:
            return normalize_collection_processing_params({})
