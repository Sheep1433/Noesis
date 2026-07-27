from types import SimpleNamespace

from evals.agent.runtime import AgentEventCollector


def test_agent_event_collector_builds_common_manifest():
    collector = AgentEventCollector()
    collector.consume({"event": "on_tool_start", "name": "search_knowledge_base", "run_id": "1"})
    collector.consume(
        {
            "event": "on_tool_end",
            "name": "search_knowledge_base",
            "run_id": "1",
            "data": {"output": SimpleNamespace(content='{"hits": []}')},
        }
    )
    collector.consume(
        {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="answer")}}
    )
    collector.consume(
        {
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(usage_metadata={"input_tokens": 3, "output_tokens": 2})},
        }
    )
    collector.consume({"type": "__tw_finish__", "finish_reason": "stop"})

    manifest = collector.result(
        run_id="run-1", suite="test", subject="agent", latency_ms=10
    ).to_manifest()
    assert manifest["schema_version"] == "noesis-eval-run/v1"
    assert manifest["completed"] is True
    assert manifest["final_text"] == "answer"
    assert manifest["tool_stats"] == {"search_knowledge_base": 1}
    assert manifest["tool_outputs"][0]["output"] == '{"hits": []}'
    assert manifest["input_tokens"] == 3
    assert manifest["output_tokens"] == 2
