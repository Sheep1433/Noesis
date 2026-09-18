"""DB 跑次采集回归：driver 的 SSE 工具采集器 + CLI 终值 record 组装。

DB 跑次（super）下评测记录不从流式文本重新拼接：final_text 来自
ChannelRunResult（DB 权威），工具调用从 SSE 载荷提取，本文件钉住这两块
契约（曾因 output 帧无 name、stats-update 为累计口径踩坑）。
"""

from evals.agent.cli_driver import SseToolCollector
from noesis_cli.db_run import _build_record


def _result(**kw):
    from types import SimpleNamespace

    base = dict(
        session_id="s1",
        assistant_message_id="m1",
        plain_text="报告正文",
        finish_reason="stop",
        hitl_pending=False,
        hitl_payload=None,
        run_id="r1",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestSseToolCollector:
    def test_tool_input_output_pairing(self):
        """output 帧不带 name：靠 tool_call_id 回配 input 帧的 name/input。"""
        c = SseToolCollector()
        c.consume_data({
            "type": "tool-input-available",
            "tool_call_id": "t1", "name": "web_search",
            "input": {"query": "mcp"},
        })
        c.consume_data({
            "type": "tool-output-available",
            "tool_call_id": "t1", "output": "结果",
        })
        assert c.tool_stats == {"web_search": 1}
        assert c.tool_outputs == [
            {"name": "web_search", "input": {"query": "mcp"}, "output": "结果"}
        ]

    def test_output_without_input_falls_back(self):
        c = SseToolCollector()
        c.consume_data({"type": "tool-output-available", "tool_call_id": "t9", "output": "x"})
        assert c.tool_stats == {"unknown": 1}
        assert c.tool_outputs[0]["input"] is None

    def test_stats_update_last_wins(self):
        c = SseToolCollector()
        c.consume_data({"type": "stats-update", "input_tokens": 10, "output_tokens": 2})
        c.consume_data({"type": "stats-update", "input_tokens": 9_500_000,
                        "uncached_input_tokens": 400_000, "output_tokens": 88_000})
        assert c.session_usage["uncached_input_tokens"] == 400_000
        assert c.session_usage["input_tokens"] == 9_500_000


class TestBuildRecord:
    def test_normal_completion(self):
        record = _build_record(
            result=_result(),
            run_id="r1",
            terminal={"usage": {"input_tokens": 100, "output_tokens": 20},
                      "model_calls": [{"step": 1}]},
            latency_ms=1234,
        )
        assert record["completed"] is True
        assert record["final_text"] == "报告正文"
        assert record["run_id"] == "r1"
        assert record["input_tokens"] == 100
        assert record["output_tokens"] == 20
        assert len(record["model_calls"]) == 1

    def test_terminal_error_wins(self):
        record = _build_record(
            result=_result(), run_id="r1",
            terminal={"error": "上游网关限流"}, latency_ms=1,
        )
        assert record["completed"] is False
        assert record["error"] == "上游网关限流"

    def test_hitl_pending_marked_failed(self):
        record = _build_record(
            result=_result(hitl_pending=True, finish_reason="hitl_pending"),
            run_id="r1", terminal={}, latency_ms=1,
        )
        assert record["completed"] is False
        assert "hitl_pending" in record["error"]

    def test_empty_completion_flagged(self):
        """completed 但零文本零工具 → 改判失败（上游故障守卫）。"""
        record = _build_record(
            result=_result(plain_text=""), run_id="r1", terminal={}, latency_ms=1,
        )
        assert record["completed"] is False
        assert "empty completion" in record["error"]
