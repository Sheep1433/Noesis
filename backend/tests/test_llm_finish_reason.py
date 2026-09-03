"""provider finish_reason 归一化：网关重复 chunk 拼接折叠 + 消费方接入。"""
import pytest
from langchain_core.messages import AIMessage

from noesis.agents.middlewares.llm_error_handling_middleware import (
    warn_truncated_tool_calls,
)
from noesis.llm.finish_reason import (
    PROVIDER_FINISH_REMAP,
    normalize_provider_finish_reason,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 正常单值
        ("stop", "stop"),
        ("length", "length"),
        ("tool_calls", "tool_calls"),
        # 网关重复 chunk 被 LangChain merge_dicts 拼接后的形态
        ("stopstop", "stop"),
        ("tool_callstool_calls", "tool_calls"),
        ("tool_callstool_callstool_calls", "tool_calls"),
        ("lengthlength", "length"),
        ("max_tokensmax_tokens", "max_tokens"),
        ("content_filtercontent_filter", "content_filter"),
        ("end_turnend_turn", "end_turn"),
        # 边界
        (" stop ", "stop"),
        ("", ""),
        (None, ""),
        (123, ""),
        # 非整重复的未知值：原样返回，不做猜测式改写
        ("unknown_reason", "unknown_reason"),
        ("stops", "stops"),
    ],
)
def test_normalize_provider_finish_reason(raw, expected):
    assert normalize_provider_finish_reason(raw) == expected


def test_remap_still_keys_on_collapsed_values():
    """截断/安全两类纠偏键在折叠后的值上必须仍然命中。"""
    assert PROVIDER_FINISH_REMAP.get(normalize_provider_finish_reason("lengthlength")) == "length_stop"
    assert PROVIDER_FINISH_REMAP.get(normalize_provider_finish_reason("content_filtercontent_filter")) == "safety_stop"


def test_truncation_warning_survives_gateway_duplication():
    """finish_reason=lengthlength（重复拼接）的工具调用截断告警不得漏报。"""
    from loguru import logger as loguru_logger

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "name": "write_file",
                "args": {"path": "/workspace/report.md", "content": "# truncated"},
            }
        ],
        response_metadata={"finish_reason": "lengthlength"},
    )
    messages = []
    handler_id = loguru_logger.add(messages.append, level="WARNING")
    try:
        warn_truncated_tool_calls([message])
    finally:
        loguru_logger.remove(handler_id)
    assert any("finish_reason=length" in str(m) for m in messages)
