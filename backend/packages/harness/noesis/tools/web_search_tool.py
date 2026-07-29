"""Web 搜索与抓取 Tool（深度研究等场景）。"""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

from langchain_core.tools import StructuredTool
from langchain.tools import ToolRuntime
from pydantic import BaseModel, Field

from noesis.tools.web_providers.resolver import resolve_web_fetch, resolve_web_search
from noesis.runtime.logging import logger
from noesis.errors.tool_failure import ToolNetworkError, ToolValidationError
from noesis.runtime.evidence import EvidenceEnvelope, current_retrieval_manifest


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


def _canonical_url(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("invalid web evidence URL")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def _register_web_result(item: dict, *, tool_call_id: str) -> dict:
    url = _canonical_url(str(item.get("url") or ""))
    title = str(item.get("title") or url)
    excerpt = str(item.get("snippet") or item.get("excerpt") or "").strip() or title
    row = {**item, "source_type": "web", "url": url, "title": title, "excerpt": excerpt, "citable": True}
    manifest = current_retrieval_manifest()
    if manifest is not None:
        entry = manifest.register(EvidenceEnvelope(source_type="web", url=url, title=title, excerpt=excerpt), tool_call_id=tool_call_id)
        row.update(entry.model_dump(mode="json"))
    return row


def web_search(query: str, limit: int = 8, runtime: ToolRuntime = None) -> str:
    """关键词 Web 搜索，返回 JSON 结果列表。"""
    try:
        result = resolve_web_search(query, limit)
        if result.get("error"):
            detail = str(result.get("detail") or result.get("error"))
            if "不能为空" in str(result.get("error")):
                raise ToolValidationError(detail)
            raise ToolNetworkError(detail)
        tool_call_id = str(runtime.tool_call_id or "") if runtime is not None else ""
        registered = []
        for item in result.get("results") or []:
            try:
                registered.append(_register_web_result(item, tool_call_id=tool_call_id))
            except (TypeError, ValueError) as exc:
                logger.info("忽略不可引用的 Web 搜索结果: {}", exc)
        result["results"] = registered
        result["total_results"] = len(registered)
        return json.dumps(result, ensure_ascii=False)
    except (ToolNetworkError, ToolValidationError):
        raise
    except Exception as e:
        logger.warning("web_search 未预期异常: {}", e)
        raise ToolNetworkError(str(e) or "搜索失败") from e


def web_fetch(url: str, runtime: ToolRuntime = None) -> str:
    """抓取已知 URL 的正文摘要（Markdown）。"""
    try:
        result = resolve_web_fetch(url)
        try:
            payload = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            detail = str(payload.get("error"))
            if "不能为空" in detail or "不支持" in detail:
                raise ToolValidationError(detail)
            raise ToolNetworkError(detail)
        canonical_url = _canonical_url(url)
        title = next((line[2:].strip() for line in result.splitlines() if line.startswith("# ")), canonical_url)
        row = _register_web_result({"url": canonical_url, "title": title, "snippet": result}, tool_call_id=str(runtime.tool_call_id or "") if runtime is not None else "")
        return json.dumps({"url": canonical_url, "content": result, "results": [row]}, ensure_ascii=False)
    except (ToolNetworkError, ToolValidationError):
        raise
    except Exception as e:
        logger.warning("web_fetch 未预期异常: {}", e)
        raise ToolNetworkError(str(e) or "页面抓取失败") from e


def build_web_search_tools() -> list:
    """构建 web_search + web_fetch；由 COMMON_QA / SUPER_AGENT Agent 按需挂载。"""
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
