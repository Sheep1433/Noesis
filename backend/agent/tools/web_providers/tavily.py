"""Tavily 搜索与 extract Provider。"""

from __future__ import annotations

from typing import Any

from config.env import WebToolsConfig
from utils.log_util import logger


def _get_client():
    from tavily import TavilyClient

    api_key = (WebToolsConfig.tavily_api_key or "").strip()
    if not api_key:
        raise ValueError("TAVILY_API_KEY 未配置")
    return TavilyClient(api_key=api_key)


def tavily_available() -> bool:
    return bool((WebToolsConfig.tavily_api_key or "").strip())


def search_with_tavily(query: str, limit: int) -> dict[str, Any]:
    client = _get_client()
    res = client.search(query, max_results=limit)
    results = []
    for item in res.get("results") or []:
        results.append(
            {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "snippet": item.get("content") or item.get("snippet") or "",
            }
        )
    return {
        "query": query,
        "provider": "tavily",
        "total_results": len(results),
        "results": results,
    }


def fetch_with_tavily(url: str, max_chars: int) -> dict[str, Any]:
    client = _get_client()
    res = client.extract([url])
    failed = res.get("failed_results") or []
    if failed:
        err = failed[0].get("error") or "Tavily extract 失败"
        raise RuntimeError(str(err))

    items = res.get("results") or []
    if not items:
        raise RuntimeError("Tavily extract 无结果")

    item = items[0]
    title = item.get("title") or url
    body = (item.get("raw_content") or item.get("content") or "")[:max_chars]
    markdown = f"<!-- provider: tavily -->\n# {title}\n\n{body}"
    return {"provider": "tavily", "url": url, "markdown": markdown}
