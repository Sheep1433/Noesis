"""知识库模块端口（Protocol），便于测试替换适配器。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class KbSearchHitLike(Protocol):
    id: str
    score: float
    content: str
    file_name: str
    search_mode: str
    header_path: Optional[str]


class KbRetrieverPort(Protocol):
    def search(
        self,
        *,
        collection_name: str,
        query: str,
        search_mode: str = "vector",
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
        rrf_k: int = 60,
        vector_dimension: int = 1024,
    ) -> List[KbSearchHitLike]: ...
