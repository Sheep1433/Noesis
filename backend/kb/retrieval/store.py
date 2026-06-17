"""
优化后的向量存储和检索模块 - 职责清晰分离

VectorStore: 专注于向量存储的底层操作（创建、存储、基础查询）
Retrieval: 专注于检索策略和文档处理（embedding、多种检索策略）
"""

import hashlib
import os
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    VectorParams,
    Distance,
    PointStruct,
    Filter,
    MatchValue,
    FieldCondition,
)
from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever

from kb.embedding import get_embedding
from common.logging import logger


def kb_bm25_preprocess(text: str) -> List[str]:
    """
    BM25 分词：中文用 jieba search 模式，避免默认按空格切分导致「登录」无法命中「登录页」。
    """
    import jieba

    text = (text or "").strip()
    if not text:
        return []
    return [t.strip() for t in jieba.cut_for_search(text) if t and t.strip()]


# ============================================================================
# VectorStore 类 - 向量存储底层操作
# ============================================================================

class VectorStore:
    """
    向量存储管理类 - 专注于底层向量存储操作

    职责：
    1. Qdrant 客户端管理
    2. 集合（Collection）的创建和管理
    3. 向量的存储和更新（接收已 embedding 的向量）
    4. 基础的向量相似度查询
    5. Hash 去重机制

    不包含：
    - Embedding 逻辑（由 Retrieval 类负责）
    - 文档加载和切片（由 Retrieval 类负责）
    - 复杂检索策略（由 Retrieval 类负责）
    """

    def __init__(
        self,
        url: str,
        collection_name: str,
        vector_size: int = 1024,
        distance: str = "cosine"
    ):
        """
        初始化向量存储

        Args:
            url: Qdrant 服务地址
            collection_name: 集合名称
            vector_size: 向量维度
            distance: 距离度量方式 (cosine/dot/euclidean)
        """
        self.url = url
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.distance = distance

        # 初始化 Qdrant 客户端
        self.client = QdrantClient(url=url)

        # 初始化集合
        self._init_collection()

        logger.info(
            f"VectorStore 初始化完成: collection={collection_name}, "
            f"vector_size={vector_size}, distance={distance}"
        )

    def _init_collection(self):
        """初始化 Qdrant 集合"""
        distance_map = {
            "cosine": Distance.COSINE,
            "dot": Distance.DOT,
            "euclidean": Distance.EUCLID,
        }

        if not self.client.collection_exists(collection_name=self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=distance_map.get(self.distance, Distance.COSINE)
                ),
            )
            logger.info(f"创建新集合: {self.collection_name}")
        else:
            logger.info(f"集合已存在: {self.collection_name}")

    @staticmethod
    def compute_content_hash(text: str) -> str:
        """计算文本的 MD5 哈希"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_to_uuid(content_hash: str) -> str:
        """将哈希值转换为确定性 UUID"""
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, content_hash))

    def is_content_exists(self, content_hash: str) -> bool:
        """
        检查内容是否已存在（基于 hash 去重）

        Args:
            content_hash: 内容哈希值

        Returns:
            是否存在
        """
        scroll_filter = Filter(
            must=[
                FieldCondition(
                    key="content_hash",
                    match=MatchValue(value=content_hash)
                )
            ]
        )

        search_result = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=scroll_filter,
            limit=1,
            with_payload=False,
            with_vectors=False,
        )

        return len(search_result[0]) > 0

    @staticmethod
    def _is_payload_too_large_error(err: Exception) -> bool:
        """
        判断是否为 Qdrant payload 超限错误（HTTP 400, payload larger than allowed）。
        """
        if not isinstance(err, UnexpectedResponse):
            return False
        # qdrant_client.http.exceptions.UnexpectedResponse 通常包含 status_code / content 等信息
        status_code = getattr(err, "status_code", None)
        if status_code != 400:
            return False
        msg = str(err)
        return ("payload" in msg.lower()) and ("larger than allowed" in msg.lower())

    def _upsert_points_resilient(
        self,
        points: List[PointStruct],
        *,
        wait: bool = True,
        batch_points: int = 256,
        min_batch_points: int = 1,
    ) -> int:
        """
        容错 upsert：
        - 先按 batch_points 做分批 upsert（降低大 payload 风险）
        - 如遇 payload 超限（400），自动对当前批次二分拆分重试，直到成功或拆到 min_batch_points 仍失败
        """
        if not points:
            return 0

        # 先做初始分批，避免一次性超大 payload
        total = 0
        i = 0
        n = len(points)
        while i < n:
            chunk = points[i : i + max(min_batch_points, batch_points)]
            total += self._upsert_points_resilient_single_batch(
                chunk,
                wait=wait,
                min_batch_points=min_batch_points,
            )
            i += len(chunk)
        return total

    def _upsert_points_resilient_single_batch(
        self,
        points_batch: List[PointStruct],
        *,
        wait: bool,
        min_batch_points: int,
    ) -> int:
        """
        对单个 batch 做 upsert；若 payload 超限则递归二分拆分。
        """
        if not points_batch:
            return 0

        try:
            self.client.upsert(
                collection_name=self.collection_name,
                points=points_batch,
                wait=wait,
            )
            return len(points_batch)
        except Exception as e:
            if not self._is_payload_too_large_error(e):
                raise

            # payload 超限：二分拆分重试
            if len(points_batch) <= min_batch_points:
                # 单条仍超限，直接抛出，避免死循环
                raise

            mid = len(points_batch) // 2
            left = points_batch[:mid]
            right = points_batch[mid:]
            added = 0
            if left:
                added += self._upsert_points_resilient_single_batch(
                    left,
                    wait=wait,
                    min_batch_points=min_batch_points,
                )
            if right:
                added += self._upsert_points_resilient_single_batch(
                    right,
                    wait=wait,
                    min_batch_points=min_batch_points,
                )
            return added

    # 新 payload 中直接存放的顶层字段（用于检索和溯源）
    _PAYLOAD_TOP_FIELDS = {
        "file_name", "source", "chunk_index", "file_type",
        "raw_text", "clean_text", "update_time", "element_type",
        "page_content", "Header_1", "Header_2", "Header_3", "Header_4",
        "domain", "business", "header_path", "source_name",
    }

    # image 类型 raw_text 最大存储长度（超出部分截断，避免 Qdrant payload 超限）
    _IMAGE_RAW_TEXT_MAX_LEN = 4096

    def add_vectors(
        self,
        texts: List[str],
        vectors: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        check_duplicates: bool = True
    ) -> int:
        """
        添加向量到存储（接收已经 embedding 的向量）。

        Payload 结构（顶层字段）：
          file_name, source, chunk_index, file_type,
          raw_text, clean_text, update_time, element_type,
          page_content, Header_1/2/3/4, domain, business,
          header_path, source_name, content_hash,
          metadata（子对象，保留用于 Qdrant 过滤兼容）

        Args:
            texts: clean_text 列表（用于 embedding 和 page_content）
            vectors: 向量列表（已经过 embedding）
            metadatas: 元数据列表
            check_duplicates: 是否检查重复

        Returns:
            实际添加的向量数量
        """
        if len(texts) != len(vectors):
            raise ValueError("texts 和 vectors 长度必须一致")

        if metadatas is None:
            metadatas = [{} for _ in texts]
        elif len(metadatas) != len(texts):
            raise ValueError("metadatas 和 texts 长度必须一致")

        now = datetime.now().isoformat()

        # 去重检查
        points_to_add = []
        for i, (text, vector, metadata) in enumerate(zip(texts, vectors, metadatas)):
            content_hash = self.compute_content_hash(text)

            if check_duplicates and self.is_content_exists(content_hash):
                continue

            metadata["content_hash"] = content_hash
            point_id = self.hash_to_uuid(content_hash)

            element_type = metadata.get("element_type", "text")
            raw_text = metadata.get("raw_text", text)
            # image chunk 的 raw_text 可能包含完整 base64 data URI，截断后仅用于溯源标记
            if element_type == "image" and len(raw_text) > self._IMAGE_RAW_TEXT_MAX_LEN:
                raw_text = raw_text[:self._IMAGE_RAW_TEXT_MAX_LEN] + "...[truncated]"

            # 构建顶层 payload 字段
            payload: Dict[str, Any] = {
                "page_content": text,
                "content_hash": content_hash,
                "file_name": metadata.get("file_name") or metadata.get("source_name", ""),
                "source": metadata.get("source", ""),
                "chunk_index": metadata.get("chunk_index", i),
                "file_type": metadata.get("file_type", ""),
                "raw_text": raw_text,
                "clean_text": metadata.get("clean_text", text),
                "update_time": metadata.get("update_time", now),
                "element_type": element_type,
                "domain": metadata.get("domain", ""),
                "business": metadata.get("business", ""),
                "header_path": metadata.get("header_path", ""),
                "source_name": metadata.get("source_name") or metadata.get("file_name", ""),
                "Header_1": metadata.get("Header_1", ""),
                "Header_2": metadata.get("Header_2", ""),
                "Header_3": metadata.get("Header_3", ""),
                "Header_4": metadata.get("Header_4", ""),
                # 保留 metadata 子对象，兼容现有 Qdrant 过滤查询
                "metadata": metadata,
            }

            point = PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )
            points_to_add.append(point)

        if not points_to_add:
            logger.info("所有内容均已存在，跳过添加")
            return 0

        # 批量写入（自动分批 + payload 超限容错）
        # 可用环境变量覆盖默认批大小，便于按不同网络/服务限制调参
        batch_points = int(os.getenv("QDRANT_UPSERT_BATCH_POINTS", "256"))
        self._upsert_points_resilient(
            points_to_add,
            wait=True,
            batch_points=max(1, batch_points),
            min_batch_points=1,
        )

        logger.info(f"成功添加 {len(points_to_add)} 个向量到 {self.collection_name}")
        return len(points_to_add)

    def similarity_search(
        self,
        query_vector: List[float],
        k: int = 5,
        score_threshold: Optional[float] = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> List[Tuple[Document, float]]:
        """
        向量相似度搜索

        Args:
            query_vector: 查询向量（已经过 embedding）
            k: 返回结果数量
            score_threshold: 分数阈值
            metadata_filter: 元数据过滤条件

        Returns:
            (Document, score) 元组列表
        """
        # 构建过滤器（优先匹配顶层字段，兼容旧 metadata 子对象）
        search_filter = None
        if metadata_filter:
            conditions = []
            for key, value in metadata_filter.items():
                top_key = key if key in self._PAYLOAD_TOP_FIELDS else f"metadata.{key}"
                conditions.append(
                    FieldCondition(key=top_key, match=MatchValue(value=value))
                )
            search_filter = Filter(must=conditions)

        # 执行搜索
        search_results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,  # 直接传入向量
            limit=k,
            query_filter=search_filter,
            score_threshold=score_threshold,
            with_payload=True  # 确保返回数据
        ).points

        # 转换为 Document 格式
        results = []
        for scored_point in search_results:
            payload = scored_point.payload or {}
            meta = self._payload_to_metadata(payload)
            meta["point_id"] = str(scored_point.id)
            doc = Document(
                page_content=payload.get("page_content", "") or payload.get("content", ""),
                metadata=meta,
            )
            results.append((doc, scored_point.score))

        return results

    def delete_by_metadata(self, metadata_filter: Dict[str, Any]) -> int:
        """
        根据元数据删除向量

        Args:
            metadata_filter: 元数据过滤条件

        Returns:
            删除的向量数量
        """
        conditions = []
        for key, value in metadata_filter.items():
            top_key = key if key in self._PAYLOAD_TOP_FIELDS else f"metadata.{key}"
            conditions.append(FieldCondition(key=top_key, match=MatchValue(value=value)))

        delete_filter = Filter(must=conditions)

        # 先查询有多少条记录
        scroll_result = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=delete_filter,
            limit=10000,
            with_payload=False,
            with_vectors=False,
        )

        count = len(scroll_result[0])

        # 执行删除
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=delete_filter,
        )

        logger.info(f"删除了 {count} 个向量")
        return count

    @staticmethod
    def _payload_to_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 Qdrant payload 转换为 Document.metadata。
        优先使用 payload 顶层的新字段，再合并 payload['metadata'] 子对象（旧格式兼容）。
        """
        # 先取旧的 metadata 子对象（可能是旧格式写入的数据）
        legacy_meta = payload.get("metadata") or {}
        if not isinstance(legacy_meta, dict):
            legacy_meta = {}

        meta = {**legacy_meta}

        # 用顶层字段覆盖（新格式优先）
        for field in (
            "file_name", "source", "chunk_index", "file_type",
            "raw_text", "clean_text", "update_time", "element_type",
            "domain", "business", "header_path", "source_name",
            "Header_1", "Header_2", "Header_3", "Header_4",
            "content_hash",
        ):
            if field in payload:
                meta[field] = payload[field]

        return meta

    def get_collection_info(self) -> Dict[str, Any]:
        """获取集合信息"""
        info = self.client.get_collection(collection_name=self.collection_name)
        return {
            "name": self.collection_name,
            "points_count": info.points_count,       # 这里的点数即为文档数
            "status": info.status,
            "config": {
                "vector_size": info.config.params.vectors.size,
                "distance": info.config.params.vectors.distance.name
            }
        }

    def load_all_documents(self, limit: int = 10000) -> List[Document]:
        """
        从向量存储中加载所有文档

        Args:
            limit: 每次滚动查询的最大数量

        Returns:
            Document 列表
        """
        all_documents = []
        offset = None

        while True:
            scroll_result = self.client.scroll(
                collection_name=self.collection_name,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )

            points, next_offset = scroll_result

            # 转换为 Document
            for point in points:
                payload = point.payload
                doc = Document(
                    page_content=payload.get("page_content", ""),
                    metadata=self._payload_to_metadata(payload),
                )
                all_documents.append(doc)

            # 如果没有更多数据，退出循环
            if next_offset is None:
                break

            offset = next_offset

        logger.info(f"从 {self.collection_name} 加载了 {len(all_documents)} 个文档")
        return all_documents


# ============================================================================
# Retrieval 类 - 检索策略和文档处理
# ============================================================================

class Retrieval:
    """
    检索器 - 专注于检索策略和文档处理

    职责：
    1. Embedding 的初始化和使用
    2. 文档的加载、切片和处理
    3. 文档的 embedding 并添加到向量存储
    4. 多种检索策略（向量、BM25、混合、RRF、MMR 等）
    5. 元数据过滤和重排

    依赖：
    - VectorStore: 底层向量存储
    - get_embedding: 平台 embedding 服务
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_model: Optional[str] = None,
        auto_load_documents: bool = False
    ):
        """
        初始化检索器

        Args:
            vector_store: 向量存储实例
            embedding_model: Embedding 模型名（默认读平台配置）
            auto_load_documents: 是否自动从向量存储加载现有文档以重建 BM25 索引
        """
        self.vector_store = vector_store
        self.embedding_model = embedding_model
        self.embedding = get_embedding(embedding_model)

        # BM25 检索器（延迟初始化）
        self.bm25_retriever: Optional[BM25Retriever] = None
        self.documents: List[Document] = []

        # 自动加载现有文档（用于服务重启后恢复 BM25 索引）
        if auto_load_documents:
            self.load_existing_documents()

        logger.info(
            f"Retrieval 初始化完成: embedding_model={embedding_model or 'default'}, "
            f"auto_load={auto_load_documents}"
        )

    def add_documents(
        self,
        documents: List[Document],
        check_duplicates: bool = True,
        add_chunk_index: bool = True,
    ) -> int:
        """
        直接添加 Document 对象列表。

        优先使用 metadata 中的 clean_text 做 embedding；
        若不存在则退化为 page_content。

        Args:
            documents: Document 列表
            check_duplicates: 是否检查重复
            add_chunk_index: 是否自动补充 chunk_index

        Returns:
            实际添加的文档数量
        """
        if not documents:
            return 0

        texts = []
        metadatas = []
        for idx, doc in enumerate(documents):
            clean_text = doc.metadata.get("clean_text") or doc.page_content
            texts.append(clean_text)
            metadata = doc.metadata.copy()
            if add_chunk_index and "chunk_index" not in metadata:
                metadata["chunk_index"] = idx
            # 确保 raw_text / clean_text 字段存在
            metadata.setdefault("raw_text", doc.page_content)
            metadata.setdefault("clean_text", clean_text)
            metadatas.append(metadata)

        logger.info(f"开始 embedding {len(texts)} 个文档...")
        vectors = self.embedding.embed_documents(texts)

        count = self.vector_store.add_vectors(
            texts=texts,
            vectors=vectors,
            metadatas=metadatas,
            check_duplicates=check_duplicates,
        )

        if count > 0:
            self.documents.extend(documents[:count])
            self._rebuild_bm25()

        logger.info(f"添加文档完成: 新增 {count} 个向量")
        return count

    def _rebuild_bm25(self):
        """重建 BM25 索引（中文 jieba 分词）"""
        if self.documents:
            self.bm25_retriever = BM25Retriever.from_documents(
                self.documents,
                preprocess_func=kb_bm25_preprocess,
            )
            logger.info(f"BM25 索引重建完成: collection={self.vector_store.collection_name}, {len(self.documents)} 个文档")

    def load_existing_documents(self, limit: int = 10000) -> int:
        """
        从向量存储加载现有文档并重建 BM25 索引

        适用场景：
        - 服务重启后恢复 BM25 索引
        - 手动触发重建索引

        Args:
            limit: 每次滚动查询的最大数量

        Returns:
            加载的文档数量
        """
        logger.info("开始从向量存储加载现有文档...")

        # 从向量存储加载所有文档
        self.documents = self.vector_store.load_all_documents(limit=limit)

        # 重建 BM25 索引
        if self.documents:
            self._rebuild_bm25()
            logger.info(f"成功加载并重建索引: {len(self.documents)} 个文档")
        else:
            logger.warning(f"向量存储中没有文档，跳过 BM25 索引重建: collection={self.vector_store.collection_name}")

        return len(self.documents)

    def vector_search(
        self,
        query: str,
        k: int = 5,
        score_threshold: Optional[float] = None,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> List[Tuple[Document, float]]:
        """
        向量检索

        Args:
            query: 查询文本
            k: 返回结果数量
            score_threshold: 分数阈值
            metadata_filter: 元数据过滤

        Returns:
            (Document, score) 元组列表
        """
        logger.info(f"向量检索: query='{query[:50]}...', k={k}")

        # Embedding 查询
        query_vector = self.embedding.embed_query(query)

        # 执行搜索
        results = self.vector_store.similarity_search(
            query_vector=query_vector,
            k=k,
            score_threshold=score_threshold,
            metadata_filter=metadata_filter
        )

        logger.info(f"向量检索完成: 返回 {len(results)} 个结果")
        return results

    def bm25_search_with_scores(self, query: str, k: int = 5) -> List[Tuple[Document, float]]:
        """
        BM25 关键词检索（含 rank_bm25 原始分数）

        Returns:
            (Document, bm25_score) 列表，按分数降序
        """
        if self.bm25_retriever is None:
            logger.warning("BM25 检索器未初始化，请先添加文档")
            return []

        logger.info(f"BM25 检索: query='{query[:50]}...', k={k}")
        retriever = self.bm25_retriever
        processed_query = retriever.preprocess_func(query)
        scores = retriever.vectorizer.get_scores(processed_query)
        ranked = sorted(
            ((int(i), float(scores[i])) for i in range(len(scores))),
            key=lambda item: item[1],
            reverse=True,
        )
        # BM25 原始分在小语料上可能为负，仍按分数排序取 top-k（勿用 score<=0 截断，否则会误返回空）
        results: List[Tuple[Document, float]] = [
            (retriever.docs[idx], float(score)) for idx, score in ranked[:k]
        ]

        logger.info(f"BM25 检索完成: 返回 {len(results)} 个结果")
        return results

    def bm25_search(self, query: str, k: int = 5) -> List[Document]:
        """BM25 关键词检索（仅文档列表，兼容旧调用）。"""
        return [doc for doc, _ in self.bm25_search_with_scores(query, k=k)]

    def rrf_search_with_scores(
        self,
        query: str,
        k: int = 5,
        rrf_k: int = 60,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[Document, float]]:
        """
        RRF（Reciprocal Rank Fusion）融合向量与 BM25 两路排序。

        对每个文档：score = Σ 1/(rrf_k + rank_i + 1)，rank_i 为在各检索列表中的名次（从 0 起）。
        """
        logger.info(
            f"RRF 混合检索: query='{query[:50]}...', k={k}, rrf_k={rrf_k}, filter={metadata_filter}"
        )

        candidate_k = max(k * 2, k)
        vector_results = [
            doc
            for doc, _ in self.vector_search(
                query, k=candidate_k, metadata_filter=metadata_filter
            )
        ]
        bm25_results = self.bm25_search(query, k=candidate_k)
        if metadata_filter:
            bm25_results = self._apply_metadata_filter(bm25_results, metadata_filter)

        doc_scores: Dict[int, float] = {}
        doc_objects: Dict[int, Document] = {}

        for rank, doc in enumerate(vector_results):
            doc_id = hash(doc.page_content)
            doc_objects[doc_id] = doc
            doc_scores[doc_id] = doc_scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank + 1)

        for rank, doc in enumerate(bm25_results):
            doc_id = hash(doc.page_content)
            doc_objects[doc_id] = doc
            doc_scores[doc_id] = doc_scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank + 1)

        sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
        results = [
            (doc_objects[doc_id], float(score))
            for doc_id, score in sorted_docs[:k]
            if doc_id in doc_objects
        ]

        logger.info(f"RRF 混合检索完成: 返回 {len(results)} 个结果")
        return results

    def hybrid_search_with_scores(
        self,
        query: str,
        k: int = 5,
        rrf_k: int = 60,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[Document, float]]:
        """混合检索：与 rrf_search_with_scores 相同（平台统一 RRF 融合）。"""
        return self.rrf_search_with_scores(
            query, k=k, rrf_k=rrf_k, metadata_filter=metadata_filter
        )

    def hybrid_search(
        self,
        query: str,
        k: int = 5,
        rrf_k: int = 60,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """混合检索（RRF 融合，仅返回文档列表）。"""
        return [doc for doc, _ in self.hybrid_search_with_scores(
            query, k=k, rrf_k=rrf_k, metadata_filter=metadata_filter
        )]

    def rrf_search(
        self,
        query: str,
        k: int = 5,
        rrf_k: int = 60,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """RRF 混合检索（与 hybrid_search 等价，保留别名）。"""
        return self.hybrid_search(query, k=k, rrf_k=rrf_k, metadata_filter=metadata_filter)

    def mmr_search(
        self,
        query: str,
        k: int = 5,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """
        MMR (Maximal Marginal Relevance) 多样性检索

        通过在相关性和多样性之间取得平衡来避免返回过于相似的结果

        Args:
            query: 查询文本
            k: 返回结果数量
            fetch_k: 候选文档数量
            lambda_mult: 多样性参数 (0=最大多样性, 1=最大相关性)
            metadata_filter: 元数据过滤

        Returns:
            Document 列表
        """
        logger.info(
            f"MMR 检索: query='{query[:50]}...', k={k}, "
            f"fetch_k={fetch_k}, lambda={lambda_mult}, filter={metadata_filter}"
        )

        # 获取候选结果
        candidates = self.vector_search(query, k=fetch_k, metadata_filter=metadata_filter)

        if not candidates:
            return []

        # Embedding 候选文档
        candidate_vectors = self.embedding.embed_documents(
            [doc.page_content for doc, _ in candidates]
        )

        # MMR 算法
        selected_indices = []
        selected_vectors = []

        for _ in range(min(k, len(candidates))):
            best_score = -float('inf')
            best_idx = -1

            for i, (doc, relevance_score) in enumerate(candidates):
                if i in selected_indices:
                    continue

                # 计算与查询的相关性
                relevance = relevance_score

                # 计算与已选文档的最大相似度
                if selected_vectors:
                    max_similarity = max(
                        self._cosine_similarity(candidate_vectors[i], sv)
                        for sv in selected_vectors
                    )
                else:
                    max_similarity = 0

                # MMR 分数
                mmr_score = lambda_mult * relevance - (1 - lambda_mult) * max_similarity

                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i

            if best_idx != -1:
                selected_indices.append(best_idx)
                selected_vectors.append(candidate_vectors[best_idx])

        results = [candidates[i][0] for i in selected_indices]

        logger.info(f"MMR 检索完成: 返回 {len(results)} 个结果")
        return results

    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        import math
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        return dot_product / (norm1 * norm2) if norm1 and norm2 else 0

    @staticmethod
    def _apply_metadata_filter(
        documents: List[Document],
        metadata_filter: Dict[str, Any]
    ) -> List[Document]:
        """
        对文档列表应用元数据过滤

        Args:
            documents: 文档列表
            metadata_filter: 元数据过滤条件

        Returns:
            过滤后的文档列表
        """
        filtered_docs = []
        for doc in documents:
            match = True
            for key, value in metadata_filter.items():
                if key not in doc.metadata:
                    match = False
                    break

                if isinstance(value, list):
                    if doc.metadata[key] not in value:
                        match = False
                        break
                else:
                    if doc.metadata[key] != value:
                        match = False
                        break

            if match:
                filtered_docs.append(doc)

        return filtered_docs

    def filter_search(
        self,
        query: str,
        filters: Dict[str, Any],
        k: int = 5,
        search_type: str = "rrf"
    ) -> List[Document]:
        """
        带元数据过滤的检索

        Args:
            query: 查询文本
            filters: 元数据过滤条件
            k: 返回结果数量
            search_type: 检索类型 (vector/bm25/hybrid/rrf)

        Returns:
            过滤后的 Document 列表
        """
        logger.info(f"元数据过滤检索: filters={filters}, search_type={search_type}")

        # 先检索更多候选
        candidate_k = k * 3

        if search_type == "vector":
            candidates = [doc for doc, _ in self.vector_search(query, k=candidate_k)]
        elif search_type == "bm25":
            candidates = self.bm25_search(query, k=candidate_k)
        elif search_type == "hybrid":
            candidates = self.hybrid_search(query, k=candidate_k)
        else:  # rrf
            candidates = self.rrf_search(query, k=candidate_k)

        # 应用元数据过滤
        filtered_docs = []
        for doc in candidates:
            match = True
            for key, value in filters.items():
                if key not in doc.metadata:
                    match = False
                    break

                if isinstance(value, list):
                    if doc.metadata[key] not in value:
                        match = False
                        break
                else:
                    if doc.metadata[key] != value:
                        match = False
                        break

            if match:
                filtered_docs.append(doc)
                if len(filtered_docs) >= k:
                    break

        logger.info(f"元数据过滤完成: 候选{len(candidates)}个, 过滤后{len(filtered_docs)}个")
        return filtered_docs

    def get_stats(self) -> Dict[str, Any]:
        """获取检索器统计信息"""
        return {
            "embedding_provider": self.embedding_provider.value,
            "documents_count": len(self.documents),
            "bm25_initialized": self.bm25_retriever is not None,
            "vector_store_info": self.vector_store.get_collection_info()
        }

    def get_content_by_title(
        self,
        title: str,
        source_name: Optional[str] = None,
        domain: Optional[str] = None,
        business: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        根据标题精准检索该标题下的所有内容块。
        支持从 Header_1 到 Header_4 的匹配。
        支持通过文档元数据（source_name、domain、business）进行过滤。

        Args:
            title: 目标章节标题（如 "技术规格"）
            source_name: 可选，文件名过滤（如 "NAIE-I 26.3 AgentCore支持与用户中途交互功能设计书.docx"）
            domain: 可选，领域过滤（如 "设计文档"）
            business: 可选，业务过滤（如 "AgentCore"）

        Returns:
            以列表形式返回结果，每个元素包含标题路径和合并后的正文
            示例: [{"header_path": "...", "content": "...", "chunks_count": 5}]
        """
        filter_info = []
        if source_name:
            filter_info.append(f"source_name={source_name}")
        if domain:
            filter_info.append(f"domain={domain}")
        if business:
            filter_info.append(f"business={business}")
        filter_str = ", ".join(filter_info) if filter_info else "无"
        logger.info(f"按标题精准检索: '{title}', 文档过滤: {filter_str}")

        # 1. 构建多字段匹配过滤器 (Header_1 OR Header_2 OR Header_3 OR Header_4)
        # 注意：Qdrant 的 Filter must + should 逻辑
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        header_keys = ["Header_1", "Header_2", "Header_3", "Header_4"]

        # 构建 should 条件列表：只要其中一个 Header 匹配即可（顶层字段）
        should_conditions = [
            FieldCondition(key=key, match=MatchValue(value=title))
            for key in header_keys
        ]

        # 构建 must 条件列表：文档过滤条件（顶层字段）
        must_conditions = []
        if source_name:
            must_conditions.append(
                FieldCondition(key="source_name", match=MatchValue(value=source_name))
            )
        if domain:
            must_conditions.append(
                FieldCondition(key="domain", match=MatchValue(value=domain))
            )
        if business:
            must_conditions.append(
                FieldCondition(key="business", match=MatchValue(value=business))
            )

        # 组合过滤器：must 条件（文档过滤）+ should 条件（标题匹配）
        # 注意：Qdrant 中，当同时有 must 和 should 时，should 条件默认就是"至少满足一个"
        if must_conditions:
            query_filter = Filter(
                must=must_conditions,
                should=should_conditions
            )
        else:
            query_filter = Filter(should=should_conditions)

        # 2. 使用 scroll 获取所有匹配的原始点（不涉及向量搜索，保证 100% 精准）
        all_points = []
        offset = None
        while True:
            res_points, next_offset = self.vector_store.client.scroll(
                collection_name=self.vector_store.collection_name,
                scroll_filter=query_filter,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            all_points.extend(res_points)
            if next_offset is None:
                break
            offset = next_offset

        if not all_points:
            logger.info(f"未找到标题为 '{title}' 的内容")
            return []

        # 3. 按标题路径（header_path）进行分组处理
        # 即使不同文章有相同标题，由于 header_path 不同（包含文件名或父级），可以区分开
        grouped_content = {}

        for point in all_points:
            payload = point.payload
            metadata = self.vector_store._payload_to_metadata(payload)
            group_key = metadata.get("header_path") or metadata.get("source_name", "unknown")

            if group_key not in grouped_content:
                grouped_content[group_key] = {
                    "header_path": group_key,
                    "source_name": metadata.get("source_name"),
                    "chunks": []
                }

            grouped_content[group_key]["chunks"].append({
                "content": payload.get("page_content", ""),
                "index": metadata.get("chunk_index", 0)
            })

        # 4. 组内排序并合并内容
        final_results = []
        for path, data in grouped_content.items():
            # 按 chunk_index 排序，确保章节内容顺序正确
            sorted_chunks = sorted(data["chunks"], key=lambda x: x["index"])
            combined_text = "\n".join([c["content"] for c in sorted_chunks])

            final_results.append({
                "header_path": data["header_path"],
                "source_name": data["source_name"],
                "content": combined_text,
                "chunks_count": len(sorted_chunks)
            })

        logger.info(f"标题检索完成，共找到 {len(final_results)} 个匹配章节")
        return final_results


# ============================================================================
# 便捷工具函数
# ============================================================================

def create_retrieval_system(
    url: str,
    collection_name: str,
    embedding_model: Optional[str] = None,
    vector_size: int = 2560,
    distance: str = "cosine",
    auto_load_documents: bool = True
) -> Retrieval:
    """
    创建完整的检索系统（包含 VectorStore + Retrieval）

    Args:
        url: Qdrant 服务地址
        collection_name: 集合名称
        embedding_model: Embedding 模型名（默认读平台配置）
        vector_size: 向量维度
        distance: 距离度量方式
        auto_load_documents: 是否自动加载现有文档以重建 BM25 索引（推荐开启）

    Returns:
        Retrieval 实例
    """
    vector_store = VectorStore(
        url=url,
        collection_name=collection_name,
        vector_size=vector_size,
        distance=distance
    )

    retrieval = Retrieval(
        vector_store=vector_store,
        embedding_model=embedding_model,
        auto_load_documents=auto_load_documents
    )

    logger.info(f"检索系统创建完成: {collection_name}")
    return retrieval
