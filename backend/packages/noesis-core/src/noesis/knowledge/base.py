"""Knowledge base abstract base, domain exceptions, and file status.

The ABC declares the unified interface currently invoked by callers
(``knowledge_base_api`` / ``kb_search_tool`` / ``KbRetrievalService``). It is a
single-implementation seam (Qdrant) isolating the core engine from the
platform HTTP layer; it SHALL NOT pre-declare methods with no caller.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class FileStatus:
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    ERROR_PARSING = "error_parsing"
    INDEXING = "indexing"
    INDEXED = "indexed"
    ERROR_INDEXING = "error_indexing"


class KnowledgeBaseException(Exception):
    """知识库统一异常基类。"""


class KBNotFoundError(KnowledgeBaseException):
    """知识库 / 集合 / 文档不存在。"""


class KBNameConflictError(KnowledgeBaseException):
    """知识库名称冲突。"""


class QdrantNotConnectedError(KnowledgeBaseException):
    """向量库未连接。"""


class KBOperationError(KnowledgeBaseException):
    """知识库操作错误。"""


class KnowledgeBase(ABC):
    """知识库抽象基类，定义统一接口。

    当前唯一实现为 ``noesis.knowledge.implementations.qdrant.QdrantService``。
    client 与连接状态由 ``KnowledgeBaseManager`` 持有。
    """

    kb_type: str = ""

    @abstractmethod
    def get_collections(self) -> List[Dict[str, Any]]:
        """列出全部知识库 Collection 及规模。"""

    @abstractmethod
    def get_collection(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """读取单个 Collection 信息；不存在返回 None。"""

    @abstractmethod
    def create_collection(self, collection_name: str, vector_dimension: int, **kwargs: Any) -> Dict[str, Any]:
        """创建 Collection。"""

    @abstractmethod
    def delete_collection(self, collection_name: str) -> Dict[str, Any]:
        """删除 Collection。"""

    @abstractmethod
    def get_collection_documents(self, collection_name: str) -> List[Dict[str, Any]]:
        """列出 Collection 内文档。"""

    @abstractmethod
    def upload_document(self, *args: Any, **kwargs: Any) -> Any:
        """上传并入库文档。"""

    @abstractmethod
    def delete_document(self, collection_name: str, file_name: str) -> Dict[str, Any]:
        """删除文档。"""
