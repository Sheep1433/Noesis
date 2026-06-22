"""MCP 工具 invoke 包装：连接/鉴权失败显式抛出 ToolFailureError。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from domain.chat.streaming.tool_errors import (
    ToolFailureCategory,
    ToolFailureError,
    ToolInfrastructureError,
    ToolNetworkError,
    ToolPermissionError,
)

T = TypeVar("T")


def _translate_mcp_exception(exc: BaseException) -> BaseException:
    if isinstance(exc, ToolFailureError):
        return exc
    if isinstance(exc, httpx.ConnectError):
        return ToolNetworkError(
            str(exc) or "MCP 连接失败",
            category=ToolFailureCategory.NETWORK_UNREACHABLE,
        )
    if isinstance(exc, httpx.ConnectTimeout):
        return ToolNetworkError(
            str(exc) or "MCP 连接超时",
            category=ToolFailureCategory.NETWORK_TIMEOUT,
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ToolPermissionError(str(exc) or f"MCP 鉴权失败 HTTP {status}")
        if status >= 500:
            return ToolInfrastructureError(str(exc) or f"MCP 服务不可用 HTTP {status}")
    if isinstance(exc, (ConnectionRefusedError, ConnectionError)):
        return ToolNetworkError(
            str(exc) or "MCP 连接失败",
            category=ToolFailureCategory.NETWORK_UNREACHABLE,
        )
    if isinstance(exc, PermissionError):
        return ToolPermissionError(str(exc) or "MCP 权限不足")
    return exc


def _wrap_callable(
    fn: Callable[..., T],
    *,
    is_async: bool,
) -> Callable[..., T]:
    if is_async:

        async def _async_wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await fn(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                translated = _translate_mcp_exception(exc)
                if translated is not exc:
                    raise translated from exc
                raise

        return _async_wrapper  # type: ignore[return-value]

    def _sync_wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            translated = _translate_mcp_exception(exc)
            if translated is not exc:
                raise translated from exc
            raise

    return _sync_wrapper


def wrap_mcp_tool(tool: Any) -> Any:
    """包装单个 MCP 工具的 invoke / ainvoke / coroutine。"""
    if hasattr(tool, "invoke") and callable(tool.invoke):
        tool.invoke = _wrap_callable(tool.invoke, is_async=False)
    if hasattr(tool, "ainvoke") and callable(tool.ainvoke):
        tool.ainvoke = _wrap_callable(tool.ainvoke, is_async=True)
    coroutine = getattr(tool, "coroutine", None)
    if coroutine is not None and callable(coroutine):
        tool.coroutine = _wrap_callable(coroutine, is_async=True)
    return tool


def wrap_mcp_tools(tools: list[Any]) -> list[Any]:
    """包装 get_tools() 返回的全部 MCP 工具。"""
    return [wrap_mcp_tool(t) for t in tools]
