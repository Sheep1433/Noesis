"""知识库集合配置服务。"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from kb.chunk.params import (
    deep_merge_mapping,
    normalize_collection_processing_params,
    normalize_collection_query_params,
)
from models.kb_models import TKbCollectionConfig
from services.qdrant_service import QdrantService, is_qdrant_connected
from common.logging import logger

_sync_engine: Engine | None = None
_SyncSessionLocal: sessionmaker[Session] | None = None


def _get_sync_session() -> Session:
    """Agent 工具线程内读取集合配置；避免 asyncio.run 与全局 async 引擎跨 loop 冲突。"""
    global _sync_engine, _SyncSessionLocal
    if _SyncSessionLocal is None:
        from config.database import SYNC_SQLALCHEMY_DATABASE_URL
        from config.env import DataBaseConfig

        _sync_engine = create_engine(
            SYNC_SQLALCHEMY_DATABASE_URL,
            echo=DataBaseConfig.db_echo,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            pool_recycle=DataBaseConfig.db_pool_recycle,
            pool_timeout=DataBaseConfig.db_pool_timeout,
        )
        _SyncSessionLocal = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)
    return _SyncSessionLocal()


class KbCollectionConfigService:
    @classmethod
    def platform_processing_defaults(cls) -> Dict[str, Any]:
        return normalize_collection_processing_params({})

    @classmethod
    def platform_query_defaults(cls) -> Dict[str, Any]:
        return normalize_collection_query_params({})

    @classmethod
    async def get_row(
        cls,
        db: AsyncSession,
        collection_name: str,
    ) -> Optional[TKbCollectionConfig]:
        name = (collection_name or "").strip()
        if not name:
            return None
        result = await db.execute(
            select(TKbCollectionConfig).where(TKbCollectionConfig.collection_name == name)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_config(
        cls,
        db: AsyncSession,
        collection_name: str,
    ) -> Optional[Dict[str, Any]]:
        row = await cls.get_row(db, collection_name)
        if row is None:
            return None
        return {
            "collection_name": row.collection_name,
            "processing_params": normalize_collection_processing_params(row.processing_params),
            "query_params": normalize_collection_query_params(row.query_params),
        }

    @classmethod
    async def create_default(
        cls,
        db: AsyncSession,
        collection_name: str,
    ) -> TKbCollectionConfig:
        name = (collection_name or "").strip()
        existing = await cls.get_row(db, name)
        if existing is not None:
            return existing
        row = TKbCollectionConfig(
            collection_name=name,
            processing_params=cls.platform_processing_defaults(),
            query_params=cls.platform_query_defaults(),
        )
        db.add(row)
        await db.flush()
        logger.info(f"[KbCollectionConfig] 创建默认配置: {name}")
        return row

    @classmethod
    async def delete_config(
        cls,
        db: AsyncSession,
        collection_name: str,
    ) -> bool:
        row = await cls.get_row(db, collection_name)
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        logger.info(f"[KbCollectionConfig] 删除配置: {collection_name}")
        return True

    @classmethod
    async def patch_config(
        cls,
        db: AsyncSession,
        collection_name: str,
        *,
        processing_params: Optional[Mapping[str, Any]] = None,
        query_params: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        row = await cls.get_row(db, collection_name)
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

        await db.flush()
        return await cls.get_config(db, collection_name)

    @classmethod
    async def ensure_defaults_for_qdrant_collections(cls, db: AsyncSession) -> int:
        """为 Qdrant 已有但关系库缺失的 collection 回填默认配置行。"""
        if not is_qdrant_connected():
            return 0

        service = QdrantService()
        created = 0
        for col in service.get_collections():
            name = (col.get("name") or "").strip()
            if not name:
                continue
            row = await cls.get_row(db, name)
            if row is None:
                await cls.create_default(db, name)
                created += 1
        if created:
            logger.info(f"[KbCollectionConfig] 回填 {created} 个集合配置")
        return created

    @classmethod
    def _load_query_params_sync_db(cls, collection_name: str) -> Dict[str, Any]:
        name = (collection_name or "").strip()
        with _get_sync_session() as db:
            row = db.execute(
                select(TKbCollectionConfig).where(TKbCollectionConfig.collection_name == name)
            ).scalar_one_or_none()
            if row is None:
                return cls.platform_query_defaults()
            return normalize_collection_query_params(row.query_params)

    @classmethod
    def load_query_params_sync(cls, collection_name: str) -> Dict[str, Any]:
        """Agent 同步上下文读取集合 query_params；失败时回退平台默认。"""
        try:
            return cls._load_query_params_sync_db(collection_name)
        except Exception as exc:
            logger.warning(
                f"[KbCollectionConfig] 同步读取 query_params 失败 collection={collection_name}: {exc}"
            )
            return cls.platform_query_defaults()

    @classmethod
    def _load_processing_params_sync_db(cls, collection_name: str) -> Dict[str, Any]:
        name = (collection_name or "").strip()
        with _get_sync_session() as db:
            row = db.execute(
                select(TKbCollectionConfig).where(TKbCollectionConfig.collection_name == name)
            ).scalar_one_or_none()
            if row is None:
                return cls.platform_processing_defaults()
            return normalize_collection_processing_params(row.processing_params)

    @classmethod
    def load_processing_params_sync(cls, collection_name: str) -> Dict[str, Any]:
        try:
            return cls._load_processing_params_sync_db(collection_name)
        except Exception as exc:
            logger.warning(
                f"[KbCollectionConfig] 同步读取 processing_params 失败 collection={collection_name}: {exc}"
            )
            return cls.platform_processing_defaults()
