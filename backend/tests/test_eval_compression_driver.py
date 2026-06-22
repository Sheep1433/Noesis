"""压缩 driver 单元测试（mock 摘要 LLM）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage

from evals.compression.driver import compress_fixture_messages, parse_fixture_messages
from evals.compression.fixture_loader import load_fixture


def test_compress_fixture_messages_with_mock_summary():
    fixture = load_fixture("debug_session")
    messages = parse_fixture_messages(fixture["messages"])

    cfg = SimpleNamespace(
        summarization_enabled=True,
        context_max_input_tokens=8000,
        summarization_trigger_tokens=0,
        summarization_trigger_fraction=0.85,
        summarization_messages_to_keep=4,
        summarization_tool_offload_threshold=50,
        summarization_max_retention_ratio=0.6,
    )
    mock_model = MagicMock()
    mock_model.profile = {"max_input_tokens": 8000}

    def fake_summary(_msgs):
        return HumanMessage(content="[Conversation Summary]\n根因: max_size=0\n修复: config/database.yaml")

    with (
        patch("evals.compression.driver.ModelConfig", cfg),
        patch("agent.middlewares.context_metrics.ModelConfig", cfg),
        patch("evals.compression.driver.get_llm", return_value=mock_model),
        patch(
            "agent.middlewares.summary_offload_middleware.SummarizationOffloadMiddleware._create_summary",
            side_effect=fake_summary,
        ),
    ):
        result = compress_fixture_messages(messages, compress_options={"force": True})

    assert result["compressed"] is True
    assert result["post_message_count"] <= result["pre_message_count"]
    assert result["post_tokens"] < result["pre_tokens"]
    assert "max_size" in (result.get("summary_text") or "")
