"""Knowledge 子系统运行管理器。

管理器是 Qdrant client 与连接状态的唯一拥有者；具体向量操作仍由
``QdrantService`` 完成。这样 service、Agent 工具和评测共享同一生命周期，
同时避免 adapter 模块保存可变全局状态。
"""
from __future__ import annotations

from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

from noesis.config.env import QdrantConfig
from noesis.knowledge.base import KnowledgeBase
from noesis.knowledge.factory import KnowledgeBaseFactory
from noesis.runtime.logging import logger


class KnowledgeBaseManager:
    """拥有 Knowledge client 生命周期并提供当前实现。"""

    def __init__(self) -> None:
        self._client: Optional[QdrantClient] = None
        self._connected = False

    @property
    def client(self) -> Optional[QdrantClient]:
        return self._client

    @property
    def connected(self) -> bool:
        return self._connected

    def service(self) -> KnowledgeBase:
        """通过 factory 返回绑定当前 client 的 Knowledge 实现。"""
        return KnowledgeBaseFactory.create("qdrant", self._client)

    async def initialize(self) -> bool:
        """创建并验证 Qdrant client；重复调用保持幂等。"""
        if self._connected and self._client is not None:
            return True

        client: Optional[QdrantClient] = None
        logger.info(
            "正在连接 Qdrant: {}:{}",
            QdrantConfig.qdrant_host,
            QdrantConfig.qdrant_port,
        )
        try:
            client = QdrantClient(
                url=f"http://{QdrantConfig.qdrant_host}:{QdrantConfig.qdrant_port}",
                api_key=QdrantConfig.qdrant_api_key or None,
                timeout=QdrantConfig.qdrant_timeout,
                grpc_port=QdrantConfig.qdrant_grpc_port or None,
                prefer_grpc=QdrantConfig.qdrant_prefer_grpc,
            )
            client.get_collections()
        except UnexpectedResponse as exc:
            if client is not None:
                client.close()
            logger.error("Qdrant 连接失败 (UnexpectedResponse): {}", exc)
            return False
        except Exception as exc:
            if client is not None:
                client.close()
            logger.error("Qdrant 连接失败: {}", exc)
            return False

        self._client = client
        self._connected = True
        logger.info("Qdrant 连接成功")
        return True

    async def close(self) -> None:
        """关闭 client 并清空连接状态。"""
        client = self._client
        self._client = None
        self._connected = False
        if client is None:
            return
        try:
            client.close()
        except Exception as exc:
            logger.error("关闭 Qdrant 连接时出错: {}", exc)
        else:
            logger.info("Qdrant 连接已关闭")
