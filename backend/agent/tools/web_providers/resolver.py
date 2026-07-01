"""Tavily 优先 + DDG/local 回退解析器。"""

from __future__ import annotations

import json
from typing import Any

from agent.tools.web_providers import ddg, local_fetch, tavily
from config.env import WebToolsConfig
from common.logging import logger


def _clamp_limit(limit: int) -> int:
    default = WebToolsConfig.max_search_results
    value = limit if limit else default
    return max(1, min(20, int(value)))


def resolve_web_search(query: str, limit: int | None = None) -> dict[str, Any]:
    """执行 web_search，Tavily 优先，失败回退 DDG。"""
    q = (query or "").strip()
    if not q:
        return {"error": "query 不能为空", "query": query or ""}

    effective_limit = _clamp_limit(limit or WebToolsConfig.max_search_results)
    timeout = WebToolsConfig.fetch_timeout_seconds

    if tavily.tavily_available():
        try:
            return tavily.search_with_tavily(q, effective_limit)
        except Exception as e:
            logger.info(
                "Tavily search 不可用（{}），改用 DuckDuckGo query={!r}",
                e,
                q,
            )

    try:
        return ddg.search_with_ddg(q, effective_limit, timeout=timeout)
    except Exception as e:
        logger.warning("web_search 全部 provider 失败（DuckDuckGo）query={!r}: {}", q, e)
        return {"error": "搜索失败", "query": q}


def resolve_web_fetch(url: str) -> str:
    """执行 web_fetch，Tavily extract 优先，失败回退 local。"""
    raw_url = (url or "").strip()
    if not raw_url:
        return '{"error": "url 不能为空"}'

    max_chars = WebToolsConfig.fetch_max_chars
    timeout = WebToolsConfig.fetch_timeout_seconds

    if tavily.tavily_available():
        try:
            result = tavily.fetch_with_tavily(raw_url, max_chars)
            return result["markdown"]
        except Exception as e:
            logger.info(
                "Tavily extract 未返回内容（{}），改用 local_fetch url={}",
                e,
                raw_url,
            )

    try:
        result = local_fetch.fetch_with_local(raw_url, max_chars, timeout)
        return result["markdown"]
    except ValueError as e:
        return json.dumps({"error": str(e), "url": raw_url}, ensure_ascii=False)
    except Exception as e:
        logger.warning("web_fetch 全部 provider 失败（local_fetch）url={}: {}", raw_url, e)
        return json.dumps({"error": "页面抓取失败", "url": raw_url}, ensure_ascii=False)
