"""read_file 源头封顶单元契约：新鲜读取永不触发预算中间件的事后替换。"""

from __future__ import annotations

import inspect
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from noesis.agents.tools.read_file_bound import apply_read_file_bound


class _ReadFileArgs(BaseModel):
    file_path: str
    offset: int = 0
    limit: int = 2000


class _FakeFilesystemMiddleware:
    def __init__(self, tools: list[StructuredTool]) -> None:
        self.tools = tools


def _read_file_tool(body: str) -> StructuredTool:
    def read_file(*, file_path: str, runtime: Any, offset: int = 0, limit: int = 2000) -> ToolMessage:
        return ToolMessage(content=body, name="read_file", tool_call_id="call-1")

    async def aread_file(*, file_path: str, runtime: Any, offset: int = 0, limit: int = 2000) -> ToolMessage:
        return ToolMessage(content=body, name="read_file", tool_call_id="call-1")

    return StructuredTool.from_function(
        name="read_file",
        description="Read a file.",
        func=read_file,
        coroutine=aread_file,
        infer_schema=False,
        args_schema=_ReadFileArgs,
    )


def _fm_with(body: str) -> Any:
    return _FakeFilesystemMiddleware([_read_file_tool(body)])


def test_replacement_keeps_runtime_param_for_tool_node_injection() -> None:
    """langgraph 按 func 签名决定 runtime 注入：包装后参数名必须保留，否则真机调用缺 runtime。"""
    fm = _fm_with("content")
    apply_read_file_bound(fm, max_chars=20_000)

    assert "runtime" in inspect.signature(fm.tools[0].func).parameters
    assert "runtime" in inspect.signature(fm.tools[0].coroutine).parameters


def test_runtime_forwarded_to_original() -> None:
    """包装透传 runtime：deepagents 原函数将其作为必填参数。"""
    fm = _fm_with("short content")
    apply_read_file_bound(fm, max_chars=20_000)

    result = fm.tools[0].func(file_path="/a.txt", runtime="rt-1")

    assert result.content == "short content"


def test_oversized_output_truncated_with_offset_notice() -> None:
    fm = _fm_with("x" * 30_000)
    apply_read_file_bound(fm, max_chars=20_000)

    result = fm.tools[0].func(file_path="/a.txt", runtime="rt-1")

    assert isinstance(result, ToolMessage)
    assert len(result.content) <= 20_000
    assert "truncated at 20000 characters" in result.content
    assert "offset parameter" in result.content
    assert result.content.startswith("x")


def test_output_under_cap_passes_through_unchanged() -> None:
    body = "short content"
    fm = _fm_with(body)
    apply_read_file_bound(fm, max_chars=20_000)

    result = fm.tools[0].func(file_path="/a.txt", runtime="rt-1")

    assert result.content == body


def test_async_path_bounded_identically() -> None:
    import asyncio

    fm = _fm_with("y" * 30_000)
    apply_read_file_bound(fm, max_chars=20_000)

    result = asyncio.run(fm.tools[0].coroutine(file_path="/a.txt", runtime="rt-1"))

    assert len(result.content) <= 20_000
    assert "truncated at 20000 characters" in result.content


def test_replacement_preserves_tool_identity() -> None:
    """身份保持：工具名/描述/args schema 原样（schema 不变 = 模型调用形状不变）。"""
    original = _read_file_tool("content")
    fm = _FakeFilesystemMiddleware([original])
    apply_read_file_bound(fm, max_chars=20_000)

    bounded = fm.tools[0]
    assert bounded is not original
    assert bounded.name == original.name
    assert bounded.description == original.description
    assert bounded.args_schema is original.args_schema


def test_missing_read_file_tool_silently_skipped() -> None:
    """缺工具静默跳过：无 read_file 的中间件栈不抛错、不动其它工具。"""
    # 构造一个非 read_file 的工具栈
    def other_tool(*, path: str, runtime: Any) -> ToolMessage:
        return ToolMessage(content="ok", name="other_tool", tool_call_id="call-2")

    tool = StructuredTool.from_function(
        name="other_tool",
        description="Another tool.",
        func=other_tool,
        infer_schema=False,
    )
    fm = _FakeFilesystemMiddleware([tool])
    apply_read_file_bound(fm, max_chars=20_000)

    assert fm.tools == [tool]


def test_runtime_injection_survives_full_toolnode_chain() -> None:
    """真实调用链：包装后的 read_file 经 ToolNode runtime 注入必须能收到 runtime。

    回归（2026-09-03 问题 6）：``from __future__ import annotations`` 使包装函数的
    ``runtime: ToolRuntime`` 注解延迟为字符串，langchain 的
    ``StructuredTool._injected_args_keys`` 只检查原始注解对象（不做字符串解析），
    探测不到注入键 → ``_parse_input`` 按 args_schema 剥掉注入的 runtime →
    ``aread_file_bounded() missing 1 required positional argument: 'runtime'``。
    单测直调 ``func(runtime=...)`` 覆盖不到这条链路，必须走编译图。
    """
    import asyncio

    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    class _FakeBackend:
        def read(self, path, offset=0, limit=100):
            return "\n".join(f"L{i}: content" for i in range(offset, offset + limit))

        async def aread(self, path, offset=0, limit=100):
            return self.read(path, offset, limit)

    fm = FilesystemMiddleware(backend=_FakeBackend())
    apply_read_file_bound(fm, max_chars=20_000)
    bounded = [t for t in fm.tools if t.name == "read_file"][0]
    assert bounded._injected_args_keys == frozenset({"runtime"}), (
        "langchain 未识别 runtime 注入键（检查包装函数注解是否被 "
        "from __future__ import annotations 延迟成字符串）"
    )

    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode([bounded]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()

    msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"file_path": "/tmp/a.txt", "limit": 5},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )
    result = asyncio.run(graph.ainvoke({"messages": [msg]}))
    out = result["messages"][-1]
    assert isinstance(out, ToolMessage), f"工具链异常终止: {out!r}"
    assert "L0: content" in str(out.content)
    assert out.tool_call_id == "call-1"
