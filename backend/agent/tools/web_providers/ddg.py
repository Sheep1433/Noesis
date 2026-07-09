"""DuckDuckGo 搜索回退 Provider。"""

from __future__ import annotations

from typing import Any

from common.logging import logger
from config.env import WebToolsConfig
from domain.chat.streaming.tool_errors import ToolInfrastructureError


def _backend_attempts(configured: str | None) -> list[str]:
    """按优先级尝试的 backend 列表（去重）。"""
    primary = (configured or WebToolsConfig.ddg_backends or "mojeek,yandex").strip()
    seen: set[str] = set()
    attempts: list[str] = []
    for candidate in (primary, "mojeek", "brave", "duckduckgo"):
        if candidate and candidate not in seen:
            seen.add(candidate)
            attempts.append(candidate)
    return attempts


def _empty_result(query: str, backend_list: str) -> dict[str, Any]:
    return {
        "query": query,
        "provider": "ddg",
        "ddg_backends": backend_list,
        "total_results": 0,
        "results": [],
    }


def _is_no_results_error(exc: BaseException) -> bool:
    return "no results found" in str(exc).strip().lower()


def _rows_to_results(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for row in rows:
        results.append(
            {
                "title": row.get("title") or "",
                "url": row.get("href") or row.get("link") or "",
                "snippet": row.get("body") or row.get("snippet") or "",
            }
        )
    return results


def search_with_ddg(
    query: str,
    limit: int,
    timeout: int = 30,
    *,
    backends: str | None = None,
) -> dict[str, Any]:
    try:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, TimeoutException
    except ImportError as e:
        logger.error("ddgs 未安装: {}", e)
        raise ToolInfrastructureError("ddgs 库未安装") from e

    last_error: BaseException | None = None
    for backend_list in _backend_attempts(backends):
        ddgs = DDGS(timeout=timeout)
        try:
            raw_results = ddgs.text(query, max_results=limit, backend=backend_list)
            rows = list(raw_results) if raw_results else []
            results = _rows_to_results(rows)
            if results:
                return {
                    "query": query,
                    "provider": "ddg",
                    "ddg_backends": backend_list,
                    "total_results": len(results),
                    "results": results,
                }
            logger.info(
                "DDG 无结果 backend={} query={!r}",
                backend_list,
                query[:80],
            )
            continue
        except DDGSException as exc:
            if _is_no_results_error(exc):
                logger.info(
                    "DDG 无结果 backend={} query={!r}: {}",
                    backend_list,
                    query[:80],
                    exc,
                )
                continue
            last_error = exc
            logger.warning(
                "DDG 搜索异常 backend={} query={!r}: {}",
                backend_list,
                query[:80],
                exc,
            )
            continue
        except TimeoutException as exc:
            last_error = exc
            logger.warning(
                "DDG 搜索超时 backend={} query={!r}: {}",
                backend_list,
                query[:80],
                exc,
            )
            continue
        except Exception as exc:
            last_error = exc
            logger.warning(
                "DDG 搜索失败 backend={} query={!r}: {}",
                backend_list,
                query[:80],
                exc,
            )
            continue

    if last_error is not None:
        raise RuntimeError("DuckDuckGo 搜索失败") from last_error
    return _empty_result(query, _backend_attempts(backends)[0])
