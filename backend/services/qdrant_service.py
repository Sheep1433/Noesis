"""
Qdrant 向量库服务
"""
import os
from typing import Optional, List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList, Distance, VectorParams
from qdrant_client.http.exceptions import UnexpectedResponse

from config.env import QdrantConfig
from utils.log_util import logger
from kb.document_parse import DocumentParser
from kb.chunk import chunk, fixed_processing_params
from kb.retrieval.payload import documents_to_points

# 全局 Qdrant 客户端实例
_qdrant_client: Optional[QdrantClient] = None
_is_connected: bool = False


def get_qdrant_client() -> Optional[QdrantClient]:
    """获取 Qdrant 客户端实例"""
    return _qdrant_client


def is_qdrant_connected() -> bool:
    """检查 Qdrant 连接状态"""
    return _is_connected


async def init_qdrant_client() -> bool:
    """
    初始化 Qdrant 客户端连接
    
    Returns:
        bool: 连接是否成功
    """
    global _qdrant_client, _is_connected
    
    try:
        logger.info(f"正在连接 Qdrant: {QdrantConfig.qdrant_host}:{QdrantConfig.qdrant_port}")
        
        # 使用 url 参数明确指定 HTTP 连接，避免 SSL 问题
        _qdrant_client = QdrantClient(
            url=f"http://{QdrantConfig.qdrant_host}:{QdrantConfig.qdrant_port}",
            api_key=QdrantConfig.qdrant_api_key if QdrantConfig.qdrant_api_key else None,
            timeout=QdrantConfig.qdrant_timeout,
            grpc_port=QdrantConfig.qdrant_grpc_port if QdrantConfig.qdrant_grpc_port else None,
            prefer_grpc=QdrantConfig.qdrant_prefer_grpc,
        )
        
        # 测试连接 - 获取 collections 列表
        _qdrant_client.get_collections()
        
        _is_connected = True
        logger.info("✅ Qdrant 连接成功")
        return True
        
    except UnexpectedResponse as e:
        _is_connected = False
        logger.error(f"❌ Qdrant 连接失败 (UnexpectedResponse): {e}")
        return False
    except Exception as e:
        _is_connected = False
        logger.error(f"❌ Qdrant 连接失败: {e}")
        return False


async def close_qdrant_client() -> None:
    """关闭 Qdrant 客户端连接"""
    global _qdrant_client, _is_connected

    if _qdrant_client:
        try:
            # QdrantClient 的 close 方法是同步的
            _qdrant_client.close()
        except Exception as e:
            logger.error(f"关闭 Qdrant 连接时出错: {e}")
        finally:
            _qdrant_client = None
            _is_connected = False
            logger.info("Qdrant 连接已关闭")


class QdrantService:
    """Qdrant 服务封装类"""
    
    def __init__(self):
        self.client = _qdrant_client
    
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

        try:
            # 创建 Collection
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_dimension,
                    distance=Distance.COSINE,
                ),
            )

            logger.info(f"创建 Collection 成功: {collection_name}, vector_dimension={vector_dimension}")
            return {
                'success': True,
                'message': f"Collection '{collection_name}' 创建成功",
                'name': collection_name,
            }

        except UnexpectedResponse as e:
            # 400 可能表示已存在（本地存储已有数据）
            if e.status_code == 400:
                return {'success': False, 'message': f"Collection '{collection_name}' 已存在", 'code': 409}
            logger.error(f"创建 Collection {collection_name} 失败 (UnexpectedResponse): {e}")
            return {'success': False, 'message': f"创建失败: {str(e)}", 'code': e.status_code}
        except Exception as e:
            logger.error(f"创建 Collection {collection_name} 失败: {e}")
            return {'success': False, 'message': f"创建失败: {str(e)}"}

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
                created_at = payload.get('created_at')
                if created_at:
                    if not documents_map[file_name]['uploaded_at']:
                        documents_map[file_name]['uploaded_at'] = created_at
                    elif created_at < documents_map[file_name]['uploaded_at']:
                        documents_map[file_name]['uploaded_at'] = created_at
            
            return list(documents_map.values())
            
        except Exception as e:
            logger.error(f"获取文档列表失败: {e}")
            return []
    
    def get_document_shards(self, collection_name: str, file_name: str) -> List[Dict[str, Any]]:
        """
        获取文档的所有分片
        
        Args:
            collection_name: Collection 名称
            file_name: 文件名
            
        Returns:
            List[Dict]: 分片信息列表
        """
        if not self.client:
            return []
        
        try:
            results, _ = self.client.scroll(
                collection_name=collection_name,
                limit=10000,
                with_payload=True,
            )
            
            shards = []
            for point in results:
                payload = point.payload or {}
                if payload.get('file_name') == file_name:
                    content = payload.get('page_content') or payload.get('content', '')
                    shards.append({
                        'id': str(point.id),
                        'content': content,
                        'char_length': len(content or ''),
                        'created_at': payload.get('created_at'),
                        'header_path': payload.get('header_path') or None,
                        'chunk_index': payload.get('chunk_index'),
                    })
            
            return shards
            
        except Exception as e:
            logger.error(f"获取分片列表失败: {e}")
            return []
    
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
            )
            if not points:
                logger.warning(f"分片 {shard_id} 不存在")
                return None
            point = points[0]
            
            payload = point.payload or {}
            content = payload.get('page_content') or payload.get('content', '')
            return {
                'id': str(point.id),
                'content': content,
                'char_length': len(content or ''),
                'vector_dimension': len(point.vector) if point.vector else 0,
                'created_at': payload.get('created_at'),
                'header_path': payload.get('header_path') or None,
                'Header_1': payload.get('Header_1') or None,
                'Header_2': payload.get('Header_2') or None,
                'Header_3': payload.get('Header_3') or None,
                'chunk_index': payload.get('chunk_index'),
            }
            
        except Exception as e:
            logger.error(f"获取分片详情失败: {e}")
            return None
    
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
            ef_params = fixed_processing_params()
            try:
                parsed = DocumentParser.parse_file(file_path)
                documents = chunk(parsed, effective_params=ef_params)
            except ValueError as exc:
                return {'success': False, 'message': str(exc), 'shards_created': 0}

            if not documents:
                return {'success': False, 'message': '文档解析失败或内容为空', 'shards_created': 0}

            content = self.parse_document(file_path)

            texts = [(d.page_content or "").strip() for d in documents]
            logger.info(f"文档 {file_name} 解析完成，共 {len(texts)} 个分片")

            # 4. 生成真实向量（DashScope 通义文本向量）
            from kb.embedding import get_embedding

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
                file_hash=file_hash,
                effective_processing_params=ef_params,
            )
            if not points:
                return {'success': False, 'message': '文档分片失败', 'shards_created': 0}

            self.client.upsert(
                collection_name=collection_name,
                points=points,
            )
            from kb.retrieval import KbRetrievalService

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
