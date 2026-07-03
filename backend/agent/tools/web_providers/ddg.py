"""DuckDuckGo 搜索回退 Provider。"""

from __future__ import annotations

from typing import Any

from common.logging import logger
from config.env import WebToolsConfig
from domain.chat.streaming.tool_errors import ToolInfrastructureError


def search_with_ddg(
    query: str,
    limit: int,
    timeout: int = 30,
    *,
    backends: str | None = None,
) -> dict[str, Any]:
    try:
        from ddgs import DDGS
    except ImportError as e:
        logger.error("ddgs 未安装: {}", e)
        raise ToolInfrastructureError("ddgs 库未安装") from e

    backend_list = (backends or WebToolsConfig.ddg_backends or "mojeek,yandex").strip()
    ddgs = DDGS(timeout=timeout)
    try:
        raw_results = ddgs.text(query, max_results=limit, backend=backend_list)
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
        "ddg_backends": backend_list,
        "total_results": len(results),
        "results": results,
    }
