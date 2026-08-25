"""Rebuildable semantic index derived from authoritative memory items."""

from __future__ import annotations

import asyncio
import json

from qdrant_client.http import models as qmodels
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.config.env import MachineMemoryConfig
from noesis.knowledge.embedding.embedding import get_embedding
from noesis.knowledge.runtime import knowledge_base
from noesis.repositories.machine_memory_repository import MachineMemoryRepository


_INDEX_META_ID = "00000000-0000-0000-0000-000000000000"

def index_document(item) -> str:
    return json.dumps(
        {
            "type": item.memory_type,
            "subject": item.subject,
            "statement": item.statement,
            "applicability": item.applicability,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class MemoryIndexService:
    def __init__(self, *, client=None, embedding=None):
        self.client = client or knowledge_base.client
        self.embedding = embedding

    def _require_client(self):
        if self.client is None:
            raise RuntimeError("memory semantic index is unavailable")
        return self.client

    async def _vector(self, text: str) -> list[float]:
        if self.embedding is None:
            self.embedding = get_embedding()
        return list(await asyncio.to_thread(self.embedding.embed_query, text))

    async def _write_index_metadata(self, vector_size: int) -> None:
        await asyncio.to_thread(
            self._require_client().upsert,
            collection_name=MachineMemoryConfig.collection_name,
            points=[qmodels.PointStruct(
                id=_INDEX_META_ID,
                vector=[0.0] * vector_size,
                payload={
                    "index_metadata": True,
                    "template_version": MachineMemoryConfig.embedding_template_version,
                },
            )],
            wait=True,
        )

    async def _metadata_is_current(self) -> bool:
        points = await asyncio.to_thread(
            self._require_client().retrieve,
            collection_name=MachineMemoryConfig.collection_name,
            ids=[_INDEX_META_ID],
            with_payload=True,
            with_vectors=False,
        )
        return bool(
            points
            and points[0].payload
            and points[0].payload.get("template_version")
            == MachineMemoryConfig.embedding_template_version
        )

    async def _ensure_collection(self, vector_size: int) -> bool:
        client = self._require_client()
        exists = await asyncio.to_thread(
            client.collection_exists, MachineMemoryConfig.collection_name
        )
        if not exists:
            await asyncio.to_thread(
                client.create_collection,
                collection_name=MachineMemoryConfig.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=vector_size, distance=qmodels.Distance.COSINE
                ),
            )
            await self._write_index_metadata(vector_size)
            return True
        info = await asyncio.to_thread(
            client.get_collection, MachineMemoryConfig.collection_name
        )
        vectors = info.config.params.vectors
        existing_size = getattr(vectors, "size", None)
        return existing_size == vector_size and await self._metadata_is_current()

    async def sync_item(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        scope_key: str,
        memory_id: str,
    ) -> str:
        item = await MachineMemoryRepository(db).get_item(memory_id, user_id=str(user_id))
        if (
            item is None
            or item.scope_key != scope_key
            or item.status != "active"
            or item.valid_to is not None
        ):
            await self.delete(memory_id)
            return "deleted"
        vector = await self._vector(index_document(item))
        if not await self._ensure_collection(len(vector)):
            await self.rebuild(db, vector_size=len(vector))
            return "rebuilt"
        point = qmodels.PointStruct(
            id=item.id,
            vector=vector,
            payload={
                "user_id": str(item.user_id),
                "scope_key": item.scope_key,
                "memory_type": item.memory_type,
                "status": item.status,
                "effective_provenance": item.effective_provenance,
                "version": item.version,
                "valid_from": item.valid_from.isoformat() if item.valid_from else None,
                "valid_to": item.valid_to.isoformat() if item.valid_to else None,
                "template_version": MachineMemoryConfig.embedding_template_version,
            },
        )
        await asyncio.to_thread(
            self._require_client().upsert,
            collection_name=MachineMemoryConfig.collection_name,
            points=[point],
            wait=True,
        )
        return "upserted"

    async def delete(self, memory_id: str) -> None:
        client = self._require_client()
        exists = await asyncio.to_thread(
            client.collection_exists, MachineMemoryConfig.collection_name
        )
        if not exists:
            return
        await asyncio.to_thread(
            client.delete,
            collection_name=MachineMemoryConfig.collection_name,
            points_selector=qmodels.PointIdsList(points=[memory_id]),
            wait=True,
        )

    async def delete_user(self, user_id: str) -> None:
        client = self._require_client()
        exists = await asyncio.to_thread(
            client.collection_exists, MachineMemoryConfig.collection_name
        )
        if not exists:
            return
        await asyncio.to_thread(
            client.delete,
            collection_name=MachineMemoryConfig.collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(must=[
                    qmodels.FieldCondition(
                        key="user_id", match=qmodels.MatchValue(value=str(user_id))
                    )
                ])
            ),
            wait=True,
        )

    async def search(
        self, *, query: str, user_id: str, scope_key: str, limit: int
    ) -> list[tuple[str, float]]:
        client = self._require_client()
        exists = await asyncio.to_thread(
            client.collection_exists, MachineMemoryConfig.collection_name
        )
        if not exists:
            return []
        if not await self._metadata_is_current():
            return []
        vector = await self._vector(query)
        result = await asyncio.to_thread(
            client.query_points,
            collection_name=MachineMemoryConfig.collection_name,
            query=vector,
            query_filter=qmodels.Filter(must=[
                qmodels.FieldCondition(
                    key="user_id", match=qmodels.MatchValue(value=str(user_id))
                ),
                qmodels.FieldCondition(
                    key="scope_key", match=qmodels.MatchValue(value=scope_key)
                ),
            ]),
            limit=limit,
            with_payload=False,
        )
        return [(str(point.id), float(point.score)) for point in result.points]

    async def rebuild(
        self, db: AsyncSession, *, vector_size: int | None = None
    ) -> int:
        client = self._require_client()
        exists = await asyncio.to_thread(
            client.collection_exists, MachineMemoryConfig.collection_name
        )
        if exists:
            await asyncio.to_thread(
                client.delete_collection, MachineMemoryConfig.collection_name
            )
        if vector_size is not None:
            await asyncio.to_thread(
                client.create_collection,
                collection_name=MachineMemoryConfig.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=vector_size, distance=qmodels.Distance.COSINE
                ),
            )
            await self._write_index_metadata(vector_size)
        offset = 0
        total = 0
        while True:
            items = await MachineMemoryRepository(db).list_active_items(
                offset=offset, limit=500
            )
            if not items:
                break
            for item in items:
                await self.sync_item(
                    db,
                    user_id=str(item.user_id),
                    scope_key=item.scope_key,
                    memory_id=item.id,
                )
                total += 1
            offset += len(items)
        return total


__all__ = ["MemoryIndexService", "index_document"]
