"""DuckDuckGo 搜索回退 Provider。"""

from __future__ import annotations

from typing import Any

from common.logging import logger
from domain.chat.streaming.tool_errors import ToolInfrastructureError


def search_with_ddg(query: str, limit: int, timeout: int = 30) -> dict[str, Any]:
    try:
        from ddgs import DDGS
    except ImportError as e:
        logger.error("ddgs 未安装: {}", e)
        raise ToolInfrastructureError("ddgs 库未安装") from e

    ddgs = DDGS(timeout=timeout)
    try:
        raw_results = ddgs.text(query, max_results=limit)
        rows = list(raw_results) if raw_results else []
    except Exception as e:
        logger.warning("DDG 搜索失败 query={}: {}", query[:80], e)
        raise RuntimeError("DuckDuckGo 搜索失败") from e

    results = []
    for row in rows:
        results.append(
            {
                "title": row.get("title") or "",
                "url": row.get("href") or row.get("link") or "",
                "snippet": row.get("body") or row.get("snippet") or "",
            }
        )

    return {
        "query": query,
        "provider": "ddg",
        "total_results": len(results),
        "results": results,
    }
