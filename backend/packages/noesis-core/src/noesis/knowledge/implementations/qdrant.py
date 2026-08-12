"""
Qdrant 向量库服务
"""
import base64
import hashlib
import json
from typing import Any, Dict, List, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Direction,
    Distance,
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchText,
    MatchValue,
    OrderBy,
    PayloadField,
    PayloadSchemaType,
    PointIdsList,
    Range,
    VectorParams,
)
from qdrant_client.http.exceptions import UnexpectedResponse

from noesis.runtime.logging import logger
from noesis.knowledge.parser import DocumentParser
from noesis.knowledge.chunking import chunk, build_effective_processing_snapshot, fixed_processing_params
from noesis.knowledge.retrieval.payload import documents_to_points, payload_created_at
from noesis.knowledge.base import KBNotFoundError, KBOperationError, KnowledgeBase

_CHUNK_PREVIEW_LENGTH = 320
_SAFE_RAW_METADATA_KEYS = frozenset(
    {
        "page_no",
        "page_number",
        "bbox",
        "start_index",
        "end_index",
        "layout_type",
        "section_type",
        "sheet_name",
        "slide_number",
        "row_start",
        "row_end",
    }
)

class QdrantService(KnowledgeBase):
    """Qdrant 服务封装类"""

    kb_type = "qdrant"

    def __init__(self, client: QdrantClient | None):
        self.client = client
        self._inspection_indexes_ready: set[str] = set()

    @staticmethod
    def _shard_chunk_sort_key(shard: Dict[str, Any]) -> int:
        raw = shard.get('chunk_index')
        try:
            return int(raw) if raw is not None else 10**9
        except (TypeError, ValueError):
            return 10**9

    def _collection_vector_dimension(self, collection_name: str) -> int:
        if not self.client:
            return 0
        try:
            info = self.client.get_collection(collection_name)
            if info.config and info.config.params and info.config.params.vectors:
                return int(info.config.params.vectors.size or 0)
        except Exception as e:
            logger.warning(f"获取 Collection {collection_name} 向量维度失败: {e}")
        return 0

    @staticmethod
    def _inspection_filter(
        file_name: str,
        *,
        element_type: Optional[str] = None,
        locator_type: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Filter:
        must: list[Any] = [
            FieldCondition(key="file_name", match=MatchValue(value=file_name))
        ]
        if element_type:
            must.append(
                FieldCondition(key="element_type", match=MatchValue(value=element_type))
            )
        if locator_type:
            must.append(
                FieldCondition(key="locator.type", match=MatchValue(value=locator_type))
            )
        if keyword:
            must.append(
                Filter(
                    should=[
                        FieldCondition(key="content", match=MatchText(text=keyword)),
                        FieldCondition(key="page_content", match=MatchText(text=keyword)),
                    ]
                )
            )
        return Filter(must=must)

    @staticmethod
    def _cursor_fingerprint(
        collection_name: str,
        file_name: str,
        *,
        element_type: Optional[str],
        locator_type: Optional[str],
        keyword: Optional[str],
        sort: str,
    ) -> str:
        raw = json.dumps(
            [collection_name, file_name, element_type, locator_type, keyword, sort],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _encode_cursor(payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _cursor_offset(value: Any) -> Any:
        return value if isinstance(value, int) else str(value)

    @staticmethod
    def _decode_cursor(cursor: str, fingerprint: str) -> Dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            if not isinstance(data, dict) or data.get("v") != 1:
                raise ValueError
            if data.get("fingerprint") != fingerprint:
                raise ValueError
            if data.get("phase") not in {"ordered", "missing"}:
                raise ValueError
            if data.get("chunk_index") is not None and not isinstance(
                data["chunk_index"], int
            ):
                raise ValueError
            if data.get("offset") is not None and not isinstance(
                data["offset"], (int, str)
            ):
                raise ValueError
            return data
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("列表状态已失效，请重新加载") from exc

    def _ensure_inspection_indexes(self, collection_name: str) -> None:
        if not self.client or collection_name in self._inspection_indexes_ready:
            return
        schemas = {
            "file_name": PayloadSchemaType.KEYWORD,
            "chunk_index": PayloadSchemaType.INTEGER,
            "element_type": PayloadSchemaType.KEYWORD,
            "locator.type": PayloadSchemaType.KEYWORD,
            "content": PayloadSchemaType.TEXT,
            "page_content": PayloadSchemaType.TEXT,
        }
        try:
            for field_name, field_schema in schemas.items():
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )
        except Exception as exc:
            logger.exception(f"准备 Collection {collection_name} 分片检查索引失败")
            raise KBOperationError("知识库筛选暂不可用，请稍后重试") from exc
        self._inspection_indexes_ready.add(collection_name)

    def _rollback_collection_creation(self, collection_name: str) -> None:
        """创建后的必要索引失败时删除未完成的 Collection。"""
        try:
            self.client.delete_collection(collection_name=collection_name)
        except Exception:
            logger.exception(f"回滚未完成的 Collection {collection_name} 失败")
        finally:
            self._inspection_indexes_ready.discard(collection_name)

    def _next_chunk_index(
        self,
        collection_name: str,
        base_filter: Filter,
        *,
        after: Optional[int],
        sort: str,
    ) -> Optional[int]:
        """读取下一个有序 chunk_index；同序号的 point 由 point-id scroll 分页。"""
        conditions = list(base_filter.must or [])
        if after is not None:
            conditions.append(
                FieldCondition(
                    key="chunk_index",
                    range=(
                        Range(gt=float(after))
                        if sort == "asc"
                        else Range(lt=float(after))
                    ),
                )
            )
        direction = Direction.ASC if sort == "asc" else Direction.DESC
        records, _ = self.client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=conditions),
            limit=1,
            order_by=OrderBy(key="chunk_index", direction=direction),
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return None
        raw_index = (records[0].payload or {}).get("chunk_index")
        return int(raw_index) if raw_index is not None else None

    @staticmethod
    def _chunk_summary(point: Any) -> Dict[str, Any]:
        payload = point.payload or {}
        content = str(payload.get("page_content") or payload.get("content") or "")
        preview = content[:_CHUNK_PREVIEW_LENGTH]
        if len(content) > _CHUNK_PREVIEW_LENGTH:
            preview += "…"
        token_count = payload.get("token_count")
        if token_count is None and isinstance(payload.get("metadata"), dict):
            token_count = payload["metadata"].get("token_count")
        return {
            "id": str(point.id),
            "content_preview": preview,
            "char_length": len(content),
            "created_at": payload_created_at(payload),
            "header_path": payload.get("header_path") or None,
            "chunk_index": payload.get("chunk_index"),
            "locator": payload.get("locator") or None,
            "element_type": payload.get("element_type") or None,
            "token_count": token_count if isinstance(token_count, int) else None,
        }

    def get_collections(self) -> List[Dict[str, Any]]:
        """
        获取所有 Collection 列表
        
        Returns:
            List[Dict]: Collection 信息列表
        """
        if not self.client:
            return []
        
        try:
            collections = self.client.get_collections().model_dump()
            result = []
            
            for col in collections.get('collections', []):
                name = col.get('name', '')
                try:
                    info = self.client.get_collection(name)
                    vectors_size = 0
                    if info.config and info.config.params and info.config.params.vectors:
                        vectors_size = info.config.params.vectors.size or 0
                    # 获取创建时间（如果可用）
                    created_at = None
                    if hasattr(info, 'created_at') and info.created_at:
                        created_at = info.created_at.isoformat() if hasattr(info.created_at, 'isoformat') else str(info.created_at)
                    # 获取文档数量（按 file_name 分组的数量）
                    documents = self.get_collection_documents(name)
                    documents_count = len(documents)
                    result.append({
                        'name': name,
                        'vector_dimension': vectors_size,
                        'documents_count': documents_count,
                        'points_count': info.points_count or 0,
                        'created_at': created_at,
                    })
                except Exception as e:
                    logger.warning(f"获取 Collection {name} 详情失败: {e}")
                    # 跳过获取详情失败的 Collection，避免前端显示混乱
                    continue
            
            return result
            
        except Exception as e:
            logger.error(f"获取 Collection 列表失败: {e}")
            return []
    
    def get_collection(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定 Collection 详情

        Args:
            collection_name: Collection 名称

        Returns:
            Dict: Collection 详情
        """
        if not self.client:
            return None

        try:
            info = self.client.get_collection(collection_name)
            vectors_size = 0
            if info.config and info.config.params and info.config.params.vectors:
                vectors_size = info.config.params.vectors.size or 0
            created_at = None
            if hasattr(info, 'created_at') and info.created_at:
                created_at = info.created_at.isoformat() if hasattr(info.created_at, 'isoformat') else str(info.created_at)

            # 获取文档数量（按 file_name 分组的数量）
            documents = self.get_collection_documents(collection_name)
            documents_count = len(documents)

            return {
                'name': collection_name,
                'vector_dimension': vectors_size,
                'documents_count': documents_count,
                'points_count': info.points_count or 0,
                'created_at': created_at,
                'status': info.status,
            }
        except Exception as e:
            logger.error(f"获取 Collection {collection_name} 详情失败: {e}")
            return None

    def create_collection(
        self,
        collection_name: str,
        vector_dimension: int = 1024,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        创建 Collection

        Args:
            collection_name: Collection 名称
            vector_dimension: 向量维度
            description: Collection 描述

        Returns:
            Dict: 包含 success, message 字段
        """
        if not self.client:
            return {'success': False, 'message': 'Qdrant 客户端未连接'}

        try:
            # 先检查是否已存在
            existing = self.client.get_collection(collection_name)
            if existing:
                return {'success': False, 'message': f"Collection '{collection_name}' 已存在", 'code': 409}

            # 已存在但抛异常的情况
        except UnexpectedResponse as e:
            if e.status_code == 404:
                # 404 表示 collection 不存在，这是正常情况，继续创建
                pass
            else:
                # 其他异常，可能表示已存在（本地存储有但未同步）
                try:
                    self.client.get_collection(collection_name)
                    return {'success': False, 'message': f"Collection '{collection_name}' 已存在", 'code': 409}
                except UnexpectedResponse as inner:
                    if inner.status_code == 404:
                        pass  # 确实不存在，继续创建
                    else:
                        raise
                except Exception:
                    raise

        except Exception as e:
            # 其他异常，可能是本地存储有数据但获取详情失败
            logger.warning(f"检查 Collection {collection_name} 是否存在时出现异常: {e}，尝试创建")

        created = False
        try:
            # 创建 Collection
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_dimension,
                    distance=Distance.COSINE,
                ),
            )
            created = True
            self._ensure_inspection_indexes(collection_name)

            logger.info(f"创建 Collection 成功: {collection_name}, vector_dimension={vector_dimension}")
            return {
                'success': True,
                'message': f"Collection '{collection_name}' 创建成功",
                'name': collection_name,
            }

        except UnexpectedResponse as e:
            if created:
                self._rollback_collection_creation(collection_name)
                logger.error(f"创建 Collection {collection_name} 的索引失败: {e}")
                return {'success': False, 'message': '创建失败，请稍后重试', 'code': e.status_code}
            # 400 可能表示已存在（本地存储已有数据）
            if e.status_code == 400:
                return {'success': False, 'message': f"Collection '{collection_name}' 已存在", 'code': 409}
            logger.error(f"创建 Collection {collection_name} 失败 (UnexpectedResponse): {e}")
            return {'success': False, 'message': f"创建失败: {str(e)}", 'code': e.status_code}
        except Exception as e:
            if created:
                self._rollback_collection_creation(collection_name)
            logger.error(f"创建 Collection {collection_name} 失败: {e}")
            return {'success': False, 'message': '创建失败，请稍后重试'}

    def delete_collection(self, collection_name: str) -> Dict[str, Any]:
        """
        删除 Collection 及其所有数据

        Args:
            collection_name: Collection 名称

        Returns:
            Dict: 包含 success, message 字段
        """
        if not self.client:
            return {'success': False, 'message': 'Qdrant 客户端未连接'}

        try:
            self.client.delete_collection(collection_name=collection_name)
            from noesis.knowledge.retrieval import KbRetrievalService

            KbRetrievalService.invalidate_cache(collection_name)
            logger.info(f"删除 Collection 成功: {collection_name}")
            return {'success': True, 'message': f"Collection '{collection_name}' 已删除"}

        except Exception as e:
            logger.error(f"删除 Collection {collection_name} 失败: {e}")
            return {'success': False, 'message': f"删除失败: {str(e)}"}

    def get_collection_documents(self, collection_name: str) -> List[Dict[str, Any]]:
        """
        获取 Collection 下的文档列表（按 file_name 分组）
        
        Args:
            collection_name: Collection 名称
            
        Returns:
            List[Dict]: 文档信息列表
        """
        if not self.client:
            return []
        
        try:
            # 获取所有 points
            results, _ = self.client.scroll(
                collection_name=collection_name,
                limit=10000,
                with_payload=True,
            )
            
            # 按 file_name 分组，保留 file_hash 信息
            documents_map: Dict[str, Dict[str, Any]] = {}
            for point in results:
                payload = point.payload or {}
                file_name = payload.get('file_name', 'unknown')
                file_hash = payload.get('file_hash')

                if file_name not in documents_map:
                    documents_map[file_name] = {
                        'file_name': file_name,
                        'file_hash': file_hash,
                        'shard_count': 0,
                        'uploaded_at': None,
                    }

                documents_map[file_name]['shard_count'] += 1

                # 使用最早的分片时间作为上传时间（从 payload 中获取）
                created_at = payload_created_at(payload)
                if created_at:
                    if not documents_map[file_name]['uploaded_at']:
                        documents_map[file_name]['uploaded_at'] = created_at
                    elif created_at < documents_map[file_name]['uploaded_at']:
                        documents_map[file_name]['uploaded_at'] = created_at
            
            return list(documents_map.values())
            
        except Exception as e:
            logger.error(f"获取文档列表失败: {e}")
            return []
    
    def get_document_shards_page(
        self, collection_name: str, file_name: str, query: Any
    ) -> Dict[str, Any]:
        """按文件与元数据过滤读取一页 chunk，避免整篇文档全量加载。"""
        if not self.client:
            raise KBOperationError("向量库未连接")

        self._ensure_inspection_indexes(collection_name)
        keyword = (query.keyword or "").strip() or None
        fingerprint = self._cursor_fingerprint(
            collection_name,
            file_name,
            element_type=query.element_type,
            locator_type=query.locator_type,
            keyword=keyword,
            sort=query.sort,
        )
        cursor_data = (
            self._decode_cursor(query.cursor, fingerprint)
            if query.cursor
            else {"phase": "ordered"}
        )
        base_filter = self._inspection_filter(
            file_name,
            element_type=query.element_type,
            locator_type=query.locator_type,
            keyword=keyword,
        )

        try:
            total = int(
                self.client.count(
                    collection_name=collection_name,
                    count_filter=base_filter,
                    exact=True,
                ).count
            )
            if total == 0:
                has_filters = bool(
                    query.element_type or query.locator_type or keyword
                )
                document_total = total
                if has_filters:
                    document_total = int(
                        self.client.count(
                            collection_name=collection_name,
                            count_filter=self._inspection_filter(file_name),
                            exact=True,
                        ).count
                    )
                if document_total == 0:
                    raise KBNotFoundError(f"文档 '{file_name}' 不存在")
            items: list[Dict[str, Any]] = []
            next_cursor: Optional[str] = None
            phase = cursor_data["phase"]

            if phase == "ordered":
                current_index = cursor_data.get("chunk_index")
                group_offset = cursor_data.get("offset")
                if current_index is None:
                    current_index = self._next_chunk_index(
                        collection_name, base_filter, after=None, sort=query.sort
                    )

                while current_index is not None and len(items) < query.limit:
                    remaining = query.limit - len(items)
                    group_filter = Filter(
                        must=list(base_filter.must or [])
                        + [
                            FieldCondition(
                                key="chunk_index",
                                match=MatchValue(value=current_index),
                            )
                        ]
                    )
                    records, group_next = self.client.scroll(
                        collection_name=collection_name,
                        scroll_filter=group_filter,
                        limit=remaining,
                        offset=group_offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    items.extend(self._chunk_summary(record) for record in records)
                    if group_next is not None:
                        next_cursor = self._encode_cursor(
                            {
                                "v": 1,
                                "fingerprint": fingerprint,
                                "phase": "ordered",
                                "chunk_index": current_index,
                                "offset": self._cursor_offset(group_next),
                            }
                        )
                        break
                    current_index = self._next_chunk_index(
                        collection_name,
                        base_filter,
                        after=current_index,
                        sort=query.sort,
                    )
                    group_offset = None

                if next_cursor is None and current_index is not None:
                    next_cursor = self._encode_cursor(
                        {
                            "v": 1,
                            "fingerprint": fingerprint,
                            "phase": "ordered",
                            "chunk_index": current_index,
                            "offset": None,
                        }
                    )
                elif next_cursor is None:
                    remaining = query.limit - len(items)
                    missing_filter = Filter(
                        must=list(base_filter.must or [])
                        + [IsEmptyCondition(is_empty=PayloadField(key="chunk_index"))]
                    )
                    if remaining > 0:
                        missing_records, missing_next = self.client.scroll(
                            collection_name=collection_name,
                            scroll_filter=missing_filter,
                            limit=remaining,
                            with_payload=True,
                            with_vectors=False,
                        )
                        items.extend(
                            self._chunk_summary(record) for record in missing_records
                        )
                        if missing_next is not None:
                            next_cursor = self._encode_cursor(
                                {
                                    "v": 1,
                                    "fingerprint": fingerprint,
                                    "phase": "missing",
                                    "offset": self._cursor_offset(missing_next),
                                }
                            )
                    elif remaining == 0:
                        missing_count = self.client.count(
                            collection_name=collection_name,
                            count_filter=missing_filter,
                            exact=True,
                        ).count
                        if missing_count:
                            next_cursor = self._encode_cursor(
                                {
                                    "v": 1,
                                    "fingerprint": fingerprint,
                                    "phase": "missing",
                                    "offset": None,
                                }
                            )
            else:
                missing_filter = Filter(
                    must=list(base_filter.must or [])
                    + [IsEmptyCondition(is_empty=PayloadField(key="chunk_index"))]
                )
                records, missing_next = self.client.scroll(
                    collection_name=collection_name,
                    scroll_filter=missing_filter,
                    limit=query.limit,
                    offset=cursor_data.get("offset"),
                    with_payload=True,
                    with_vectors=False,
                )
                items.extend(self._chunk_summary(record) for record in records)
                if missing_next is not None:
                    next_cursor = self._encode_cursor(
                        {
                            "v": 1,
                            "fingerprint": fingerprint,
                            "phase": "missing",
                            "offset": self._cursor_offset(missing_next),
                        }
                    )

            return {"items": items, "total": total, "next_cursor": next_cursor}
        except (KBNotFoundError, ValueError):
            raise
        except Exception as exc:
            logger.exception(f"获取文档 {file_name} 分片分页失败")
            raise KBOperationError("分片列表加载失败") from exc
    
    def get_shard_detail(self, collection_name: str, shard_id: str) -> Optional[Dict[str, Any]]:
        """
        获取分片详情
        
        Args:
            collection_name: Collection 名称
            shard_id: 分片 ID
            
        Returns:
            Dict: 分片详情
        """
        if not self.client:
            return None
        
        try:
            points = self.client.retrieve(
                collection_name=collection_name,
                ids=[shard_id],
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                logger.warning(f"分片 {shard_id} 不存在")
                return None
            point = points[0]
            
            payload = point.payload or {}
            content = payload.get('page_content') or payload.get('content', '')
            vector_dimension = self._collection_vector_dimension(collection_name)
            metadata = payload.get('metadata')
            safe_metadata = {
                key: value
                for key, value in (metadata.items() if isinstance(metadata, dict) else [])
                if key in _SAFE_RAW_METADATA_KEYS
            }
            token_count = payload.get('token_count')
            if token_count is None and isinstance(metadata, dict):
                token_count = metadata.get('token_count')
            return {
                'id': str(point.id),
                'content': content,
                'char_length': len(content or ''),
                'vector_dimension': vector_dimension,
                'created_at': payload_created_at(payload),
                'header_path': payload.get('header_path') or None,
                'Header_1': payload.get('Header_1') or None,
                'Header_2': payload.get('Header_2') or None,
                'Header_3': payload.get('Header_3') or None,
                'Header_4': payload.get('Header_4') or None,
                'chunk_index': payload.get('chunk_index'),
                'locator': payload.get('locator') or None,
                'element_type': payload.get('element_type') or None,
                'token_count': token_count if isinstance(token_count, int) else None,
                'file_name': payload.get('file_name') or None,
                'file_hash': payload.get('file_hash') or None,
                'content_hash': payload.get('content_hash') or None,
                'document_id': payload.get('document_id') or None,
                'document_version_id': payload.get('document_version_id') or None,
                'segment_id': payload.get('segment_id') or None,
                'source': payload.get('source') or None,
                'raw_text': payload.get('raw_text'),
                'clean_text': payload.get('clean_text'),
                'raw_metadata': safe_metadata,
                'effective_processing_params': payload.get('effective_processing_params'),
            }
            
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return None
            logger.exception(f"获取分片 {shard_id} 详情失败")
            raise KBOperationError("chunk 详情加载失败") from exc
        except Exception as exc:
            logger.exception(f"获取分片 {shard_id} 详情失败")
            raise KBOperationError("chunk 详情加载失败") from exc
    
    def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        向量检索（底层 Qdrant 查询；HTTP/Agent 请使用 KbRetrievalService）。

        Args:
            collection_name: Collection 名称
            query_vector: 查询向量
            limit: 返回数量
            score_threshold: 相似度阈值；None 表示由 Qdrant 默认过滤

        Returns:
            List[Dict]: 检索结果
        """
        if not self.client:
            return []

        try:
            qkw: Dict[str, Any] = {
                "collection_name": collection_name,
                "query": query_vector,
                "limit": limit,
                "with_payload": True,
            }
            if score_threshold is not None:
                qkw["score_threshold"] = float(score_threshold)
            results = self.client.query_points(**qkw)

            return [
                {
                    'id': str(hit.id),
                    'score': hit.score,
                    'content': hit.payload.get('content', '')[:500] if hit.payload else '',
                    'file_name': hit.payload.get('file_name', '') if hit.payload else '',
                }
                for hit in results.points
            ]
            
        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []
    
    def delete_document(self, collection_name: str, file_name: str) -> Dict[str, Any]:
        """
        删除文档（删除所有关联分片）
        
        Args:
            collection_name: Collection 名称
            file_name: 文件名
            
        Returns:
            Dict: 包含 success, message, deleted_count 字段
        """
        if not self.client:
            return {'success': False, 'message': 'Qdrant 客户端未连接', 'deleted_count': 0}
        
        try:
            # 先获取所有关联的分片 ID
            results, _ = self.client.scroll(
                collection_name=collection_name,
                limit=10000,
                with_payload=True,
            )
            
            ids_to_delete = [
                point.id 
                for point in results 
                if point.payload and point.payload.get('file_name') == file_name
            ]
            
            if ids_to_delete:
                self.client.delete(
                    collection_name=collection_name,
                    points_selector=PointIdsList(points=ids_to_delete),
                )
                from noesis.knowledge.retrieval import KbRetrievalService

                KbRetrievalService.invalidate_cache(collection_name)
                logger.info(f"成功删除文档 {file_name}，共 {len(ids_to_delete)} 个分片")
                return {'success': True, 'message': f'文档 {file_name} 已删除', 'deleted_count': len(ids_to_delete)}
            
            logger.info(f"文档 {file_name} 不存在，无需删除")
            return {'success': True, 'message': f'文档 {file_name} 不存在', 'deleted_count': 0}
            
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return {'success': False, 'message': f'删除失败: {str(e)}', 'deleted_count': 0}

    def parse_document(self, file_path: str) -> str:
        """将文档转为 Markdown 预览文本（供上传响应展示）。"""
        return DocumentParser.convert_file_to_markdown(file_path)

    def chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """
        将文本分片
        
        Args:
            text: 输入文本
            chunk_size: 每片字符数
            overlap: 相邻片段重叠字符数
            
        Returns:
            List[str]: 分片列表
        """
        if not text:
            return []
        
        chunks = []
        start = 0
        text_length = len(text)
        
        while start < text_length:
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap
        
        return chunks

    def upload_document(
        self,
        collection_name: str,
        file_name: str,
        file_path: str,
        vector_dim: int = 1024,
        effective_processing_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """上传文档：DocumentParser 统一解析分块后写入 Qdrant。"""
        if not self.client:
            return {'success': False, 'message': 'Qdrant 客户端未连接', 'shards_created': 0}

        try:
            # 1. 计算文档 hash 并检查是否已存在
            import hashlib
            with open(file_path, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()

            # 检查是否已存在相同 hash 的文档
            existing_docs = self.get_collection_documents(collection_name)
            for doc in existing_docs:
                if doc.get('file_hash') == file_hash:
                    logger.info(f"文档 {file_name} (hash: {file_hash[:16]}...) 已存在，跳过上传")
                    extracted = self.parse_document(file_path)
                    return {
                        'success': True,
                        'message': '文档已存在，无需重复上传',
                        'shards_created': doc.get('shard_count', 0),
                        'extracted_markdown': extracted or None,
                    }

            # 2. parse → chunk 两步流水线
            ef_params = effective_processing_params or fixed_processing_params()
            parser_id = str(ef_params.get("parser_id") or "deepdoc").strip().lower()
            if parser_id != "deepdoc":
                return {
                    'success': False,
                    'message': f'仅支持 parser_id=deepdoc，收到: {parser_id}',
                    'shards_created': 0,
                }
            try:
                from noesis.knowledge.parser.cached_parse import parse_file_cached

                parsed = parse_file_cached(
                    file_path,
                    collection_name=collection_name,
                    file_hash=file_hash,
                    source_file_name=file_name,
                    parser_id=parser_id,
                )
                if parsed.deepdoc_result and parsed.deepdoc_result.deepdoc_version:
                    ef_params = dict(ef_params)
                    ef_params["deepdoc_version"] = parsed.deepdoc_result.deepdoc_version
                documents = chunk(parsed, effective_params=ef_params)
            except ValueError as exc:
                return {'success': False, 'message': str(exc), 'shards_created': 0}
            except RuntimeError as exc:
                return {'success': False, 'message': str(exc), 'shards_created': 0}

            if not documents:
                return {'success': False, 'message': '文档解析失败或内容为空', 'shards_created': 0}

            content = (parsed.clean_markdown or parsed.raw_markdown or "").strip()
            if not content:
                content = self.parse_document(file_path)

            texts = [(d.page_content or "").strip() for d in documents]
            logger.info(f"文档 {file_name} 解析完成，共 {len(texts)} 个分片")

            from noesis.knowledge.embedding import embedding_not_configured_message, get_embedding, is_embedding_configured

            if not is_embedding_configured():
                return {
                    'success': False,
                    'message': embedding_not_configured_message(),
                    'shards_created': 0,
                }

            embedding = get_embedding()
            embeddings = embedding.embed_documents(texts)

            if not embeddings:
                logger.warning(f"[QdrantService] 生成 embedding 失败，使用零向量替代")
                embeddings = [[0.0] * vector_dim for _ in texts]
            else:
                emb0 = embeddings[0] if embeddings[0] is not None else []
                emb_len = len(emb0) if isinstance(emb0, list) else 0
                if emb_len and emb_len != vector_dim:
                    return {
                        'success': False,
                        'message': (
                            f"嵌入向量维度 ({emb_len}) 与 Collection 配置维度 ({vector_dim}) 不一致，"
                            '请更换匹配模型或删除后重建集合'
                        ),
                        'code': 409,
                        'shards_created': 0,
                    }
                for j, vec in enumerate(embeddings):
                    if not isinstance(vec, list) or not vec:
                        continue
                    if len(vec) != emb_len:
                        return {
                            'success': False,
                            'message': (
                                f"同一文档内第 {j} 块嵌入维度 ({len(vec)}) 与首块 ({emb_len}) 不一致"
                            ),
                            'code': 409,
                            'shards_created': 0,
                        }

            # 5. 批量插入 Qdrant（与 VectorStore / kb.retrieval.payload 一致）
            points = documents_to_points(
                documents,
                embeddings,
                collection_name=collection_name,
                file_hash=file_hash,
                effective_processing_params=build_effective_processing_snapshot(ef_params),
            )
            if not points:
                return {'success': False, 'message': '文档分片失败', 'shards_created': 0}

            self.client.upsert(
                collection_name=collection_name,
                points=points,
            )
            from noesis.knowledge.retrieval import KbRetrievalService

            KbRetrievalService.invalidate_cache(collection_name)

            logger.info(f"文档 {file_name} 上传成功，共 {len(points)} 个分片")
            return {
                'success': True,
                'message': f'文档 {file_name} 上传成功',
                'shards_created': len(points),
                'extracted_markdown': content,
            }

        except Exception as e:
            logger.error(f"文档上传失败: {e}")
            return {'success': False, 'message': f'上传失败: {str(e)}', 'shards_created': 0}
