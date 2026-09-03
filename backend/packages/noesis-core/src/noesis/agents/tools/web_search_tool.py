"""Web 搜索与抓取 Tool（深度研究等场景）。"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from noesis.agents.tools.web_providers.resolver import resolve_web_fetch, resolve_web_search
from noesis.config.env import WebToolsConfig
from noesis.runtime.logging import logger
from noesis.errors.tool_failure import ToolNetworkError, ToolValidationError

# 超限页面的全文落盘区（agent backend 虚拟文件系统内，模型可 read_file 分段续读）
_PAGE_STORE_PREFIX = "/web_pages"
# 落盘副本上限：远超任何单次读取分页需要，防异常巨型页占满存储
_MAX_STORED_CHARS = 2_000_000


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


def _normalize_web_result(item: dict) -> dict:
    url = _canonical_url(str(item.get("url") or ""))
    title = str(item.get("title") or url)
    excerpt = str(item.get("snippet") or item.get("excerpt") or "").strip() or title
    return {**item, "source_type": "web", "url": url, "title": title, "excerpt": excerpt, "citable": True}


def web_search(query: str, limit: int = 8) -> str:
    """关键词 Web 搜索，返回 JSON 结果列表。"""
    try:
        result = resolve_web_search(query, limit)
        if result.get("error"):
            detail = str(result.get("detail") or result.get("error"))
            if "不能为空" in str(result.get("error")):
                raise ToolValidationError(detail)
            raise ToolNetworkError(detail)
        registered = []
        for item in result.get("results") or []:
            try:
                registered.append(_normalize_web_result(item))
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


def _store_full_page(backend, url: str, content: str) -> str | None:
    """全文写入 agent backend（尽力而为）；返回路径或 None。"""
    if backend is None:
        return None
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    path = f"{_PAGE_STORE_PREFIX}/{digest}.md"
    if len(content) > _MAX_STORED_CHARS:
        content = (
            content[:_MAX_STORED_CHARS]
            + f"\n\n[... 存储副本在 {_MAX_STORED_CHARS:,} 字符处截断 ...]"
        )
    try:
        result = backend.write(path, content)
    except Exception:  # noqa: BLE001 — 落盘失败不阻塞返回正文
        logger.info("web_fetch 全文落盘失败 url={}", url)
        return None
    if result is None or getattr(result, "error", None):
        return None
    return path


def _truncate_page(result: str, url: str, *, char_limit: int, backend) -> str:
    """超限页面的确定性截断：头 75% + 尾 25%（markdown 行边界对齐），
    全文落盘，页脚给出精确续读 offset——模型一次 read_file 即落进被
    省略的中间段。低于上限的页面原样返回。"""
    if len(result) <= char_limit:
        return result

    head_budget = int(char_limit * 0.75)
    tail_budget = char_limit - head_budget
    head = result[:head_budget]
    tail = result[-tail_budget:] if tail_budget > 0 else ""
    # 头部切点回退到行边界（保留换行，行号可精确计算）；尾部切点前进到行边界
    nl = head.rfind("\n")
    if nl > head_budget * 0.5:
        head = head[: nl + 1]
    if tail:
        nl = tail.find("\n")
        if 0 <= nl < tail_budget * 0.5:
            tail = tail[nl + 1:]

    stored_path = _store_full_page(backend, url, result)
    # read_file 的 offset 为 0 起始行号：头部完整行数之后即省略段起点
    middle_offset = head.count("\n")
    if not head.endswith("\n"):
        middle_offset += 1

    footer_lines = [
        "",
        "─" * 8 + " [页面截断] " + "─" * 8,
        f"已展示头部 {len(head):,} 字符 + 尾部 {len(tail):,} 字符，全文共 {len(result):,} 字符。",
    ]
    if stored_path:
        footer_lines.append(f"完整页面已保存：{stored_path}")
        footer_lines.append(
            f'续读省略的中间段：read_file file_path="{stored_path}" offset={middle_offset} limit=200'
        )
    else:
        footer_lines.append("完整页面未能存储；如需中间段请换更具体的 URL 重新抓取。")
    footer_lines.append("─" * 29)

    return (
        head
        + "\n\n[... 中间段已省略，见页脚提示 ...]\n\n"
        + tail
        + "\n"
        + "\n".join(footer_lines)
    )


def _web_fetch(url: str, backend) -> str:
    """抓取已知 URL 的正文（Markdown）；超限页头尾截断 + 全文落盘。"""
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
        bounded = _truncate_page(
            result,
            url,
            char_limit=WebToolsConfig.fetch_max_chars,
            backend=backend,
        )
        row = _normalize_web_result({"url": canonical_url, "title": title, "snippet": bounded})
        # 正文只存一份（results[0].snippet）；曾经的顶层 content 字段是同一份
        # 文本的完整拷贝，纯双倍占上下文，且无任何下游消费方
        return json.dumps({"url": canonical_url, "results": [row]}, ensure_ascii=False)
    except (ToolNetworkError, ToolValidationError):
        raise
    except Exception as e:
        logger.warning("web_fetch 未预期异常: {}", e)
        raise ToolNetworkError(str(e) or "页面抓取失败") from e


def web_fetch(url: str) -> str:
    """模块级入口（backend 不可用场景：COMMON_QA 等无文件系统装配）。"""
    return _web_fetch(url, backend=None)


def build_web_search_tools(backend=None) -> list:
    """构建 web_search + web_fetch；由 COMMON_QA / SUPER_AGENT Agent 按需挂载。

    传入 agent backend 时（Super Agent），超限页面全文落盘到 backend
    文件系统、模型可 read_file 续读；无 backend（COMMON_QA）时退化为
    纯头尾截断（页脚说明全文未存储）。
    """

    def fetch_with_backend(url: str) -> str:
        return _web_fetch(url, backend=backend)

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
            func=fetch_with_backend,
            name="web_fetch",
            description=(
                "抓取指定 http/https URL 的页面正文（Markdown）。"
                "超过长度上限的页面会保留头尾、省略中间段，并在页脚给出"
                "全文保存路径与续读方法。适用于已从 web_search 获得 URL 后的正文获取。"
            ),
            args_schema=WebFetchInput,
        ),
    ]
