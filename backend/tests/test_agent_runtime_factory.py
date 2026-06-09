"""Agent runtime factory：middleware 栈与中断后继续推理的前置条件。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.factory import build_noesis_runtime_middleware
from agent.middlewares import (
    DanglingToolCallMiddleware,
    LoopDetectionMiddleware,
    SummarizationOffloadMiddleware,
    ToolErrorHandlingMiddleware,
)
from langchain_core.messages import AIMessage

from agent.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware as DTM


def test_runtime_stack_includes_guards_when_enabled() -> None:
    cfg = SimpleNamespace(
        dangling_tool_call_repair_enabled=True,
        loop_detection_enabled=True,
        loop_detection_warn_threshold=3,
        loop_detection_hard_limit=5,
    )
    summary_mw = SummarizationOffloadMiddleware(MagicMock())

    with (
        patch("agent.factory.ModelConfig", cfg),
        patch("agent.factory.create_summary_offload_middleware", return_value=summary_mw),
    ):
        stack = build_noesis_runtime_middleware(include_tool_call_limits=False)

    types = [type(m) for m in stack]
    assert DanglingToolCallMiddleware in types
    assert SummarizationOffloadMiddleware in types
    assert LoopDetectionMiddleware in types
    assert ToolErrorHandlingMiddleware in types
    assert types.index(DanglingToolCallMiddleware) < types.index(LoopDetectionMiddleware)
    assert types.index(LoopDetectionMiddleware) < types.index(ToolErrorHandlingMiddleware)


def test_runtime_stack_respects_disable_flags() -> None:
    cfg = SimpleNamespace(
        dangling_tool_call_repair_enabled=False,
        loop_detection_enabled=False,
    )
    with (
        patch("agent.factory.ModelConfig", cfg),
        patch("agent.factory.create_summary_offload_middleware", return_value=None),
    ):
        stack = build_noesis_runtime_middleware(include_tool_call_limits=False)

    assert not any(isinstance(m, DanglingToolCallMiddleware) for m in stack)
    assert not any(isinstance(m, LoopDetectionMiddleware) for m in stack)


def test_dangling_repair_does_not_mutate_persisted_parts_shape() -> None:
    """synthetic repair 仅补丁模型输入，不改变 content.parts 结构约定。"""
    persisted = {
        "version": 1,
        "parts": [{"type": "tool", "toolCallId": "call_1", "status": "streaming"}],
    }
    mw = DTM()
    msgs = [
        AIMessage(
            content="",
            tool_calls=[{"name": "bash", "id": "call_1", "args": {}}],
        )
    ]
    patched = mw._build_patched_messages(msgs)
    assert patched is not None
    assert persisted == {
        "version": 1,
        "parts": [{"type": "tool", "toolCallId": "call_1", "status": "streaming"}],
    }
