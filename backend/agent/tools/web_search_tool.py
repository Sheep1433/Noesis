"""Web 搜索与抓取 Tool（深度研究等场景）。"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.tools.web_providers.resolver import resolve_web_fetch, resolve_web_search
from common.logging import logger


class WebSearchInput(BaseModel):
    query: str = Field(description="检索关键词或问题改写")
    limit: int = Field(
        default=8,
        ge=1,
        le=20,
        description="返回结果数量上限",
    )


class WebFetchInput(BaseModel):
    url: str = Field(description="要抓取的网页 URL（仅 http/https）")


def web_search(query: str, limit: int = 8) -> str:
    """关键词 Web 搜索，返回 JSON 结果列表。"""
    try:
        result = resolve_web_search(query, limit)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("web_search 未预期异常: %s", e)
        return json.dumps(
            {"error": "搜索失败", "query": query or ""},
            ensure_ascii=False,
        )


def web_fetch(url: str) -> str:
    """抓取已知 URL 的正文摘要（Markdown）。"""
    try:
        return resolve_web_fetch(url)
    except Exception as e:
        logger.warning("web_fetch 未预期异常: %s", e)
        raw_url = (url or "").strip()
        return json.dumps(
            {"error": "页面抓取失败", "url": raw_url},
            ensure_ascii=False,
        )


def build_web_search_tools() -> list:
    """构建 web_search + web_fetch；由 COMMON_QA / DEEP_RESEARCH Agent 按需挂载。"""
    return [
        StructuredTool.from_function(
            func=web_search,
            name="web_search",
            description=(
                "在互联网上按关键词搜索，返回标题、URL 与摘要列表（JSON）。"
                "需要最新公开信息或知识库未覆盖时优先使用。"
            ),
            args_schema=WebSearchInput,
        ),
        StructuredTool.from_function(
            func=web_fetch,
            name="web_fetch",
            description=(
                "抓取指定 http/https URL 的页面正文（Markdown，有长度截断）。"
                "适用于已从 web_search 获得 URL 后的正文获取。"
            ),
            args_schema=WebFetchInput,
        ),
    ]
