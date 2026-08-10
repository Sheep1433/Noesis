from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from noesis.errors.tool_failure import ToolFailureCategory, ToolTimeoutError
from noesis.agents.tools.mcp_invoke_wrapper import wrap_mcp_tool


def test_mcp_tool_is_marked_for_context_attribution() -> None:
    tool = wrap_mcp_tool(SimpleNamespace(), timeout_seconds=1)

    assert tool.metadata["noesis_context_source"] == "mcp"


class _SlowTool:
    async def ainvoke(self, *args, **kwargs):
        await asyncio.sleep(1)
        return "late"


@pytest.mark.asyncio
async def test_mcp_async_tool_timeout_has_stable_category() -> None:
    tool = wrap_mcp_tool(_SlowTool(), timeout_seconds=0.01)

    with pytest.raises(ToolTimeoutError) as caught:
        await tool.ainvoke({})

    assert caught.value.category == ToolFailureCategory.EXECUTION_TIMEOUT


class _CancelledTool:
    async def ainvoke(self, *args, **kwargs):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_external_cancel_is_not_reclassified_as_timeout() -> None:
    tool = wrap_mcp_tool(_CancelledTool(), timeout_seconds=1)
    task = asyncio.create_task(tool.ainvoke({}))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
