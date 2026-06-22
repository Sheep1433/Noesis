"""压缩评测集成测试（默认 skip，需真实 LLM + summarization）。"""

import os

import pytest

from evals.compression.driver import compress_fixture_messages, parse_fixture_messages
from evals.compression.fixture_loader import load_fixture, load_probes
from evals.compression.grader import grade_single_probe

pytestmark = pytest.mark.skipif(
    os.environ.get("NOESIS_COMPRESSION_EVAL_INTEGRATION") != "1",
    reason="integration: set NOESIS_COMPRESSION_EVAL_INTEGRATION=1",
)


@pytest.mark.integration
def test_compression_single_fixture_integration():
    fixture = load_fixture("debug_session")
    probes = load_probes("debug_session")
    messages = parse_fixture_messages(fixture["messages"])
    compression = compress_fixture_messages(
        messages,
        compress_options=fixture.get("compress_options"),
    )
    assert compression["compressed"] is True
    probe = probes["probes"][0]
    result = grade_single_probe(compression["compressed_messages"], probe)
    assert result["continuation_text"]
    assert "scores" in result
    assert result.get("overall_probe_score") is not None
