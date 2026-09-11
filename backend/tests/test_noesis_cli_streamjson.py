"""noesis CLI print 模式契约测试：stream-json 序列化、收集、空收场守卫。"""

import json

from noesis_cli.streamjson import (
    StreamCollector,
    event_to_stream_line,
    flag_empty_completion,
)


def _feed(collector: StreamCollector, *events: dict) -> None:
    for event in events:
        line = event_to_stream_line(event)
        assert line is not None
        # 序列化往返无损：stream-json 每行必须是合法 JSON
        collector.consume(json.loads(json.dumps(line, ensure_ascii=False)))


def test_stream_line_roundtrip_and_collection():
    collector = StreamCollector()
    _feed(
        collector,
        {"event": "on_chat_model_stream", "data": {"chunk": type("C", (), {"content": "你好"})()}},
        {"event": "on_tool_start", "name": "read_file", "run_id": "t1",
         "data": {"input": {"file_path": "/memory/goal/x.md"}}},
        {"event": "on_tool_end", "name": "read_file", "run_id": "t1",
         "data": {"output": "..."}},
        {"type": "__tw_finish__", "finish_reason": "stop", "usage": {}},
    )
    record = collector.to_record()
    assert record["completed"] is True
    assert record["final_text"] == "你好"
    # 工具入参必须保留：行为级指标靠它判 /memory 直读路径
    assert record["tool_outputs"] == [
        {"name": "read_file", "input": {"file_path": "/memory/goal/x.md"}, "output": "..."}
    ]
    assert record["tool_stats"] == {"read_file": 1}


def test_usage_accumulates_from_chat_model_end():
    collector = StreamCollector()
    _feed(
        collector,
        {"event": "on_chat_model_end", "data": {
            "output": type("O", (), {"usage_metadata": {"input_tokens": 10, "output_tokens": 2}})()}},
        {"event": "on_chat_model_end", "data": {
            "output": type("O", (), {"usage_metadata": {"input_tokens": 5, "output_tokens": 1}})()}},
        {"type": "__tw_finish__", "finish_reason": "stop"},
    )
    record = collector.to_record()
    # usage 只从 on_chat_model_end 累计；finish 携带的 usage 不重复计
    assert record["input_tokens"] == 15
    assert record["output_tokens"] == 3


def test_error_event_marks_failure():
    collector = StreamCollector()
    _feed(collector, {"type": "abort", "content": "", "finish_reason": "error"})
    assert collector.error == "agent error"
    assert collector.to_record()["completed"] is False


def test_flag_empty_completion_marks_silent_model_failure():
    payload = flag_empty_completion(
        {"completed": True, "final_text": "", "tool_stats": {}, "error": None})
    assert payload["completed"] is False
    assert "empty completion" in payload["error"]
    ok = flag_empty_completion(
        {"completed": True, "final_text": "答案", "tool_stats": {}, "error": None})
    assert ok["completed"] is True and ok["error"] is None
    ok2 = flag_empty_completion(
        {"completed": True, "final_text": "", "tool_stats": {"read_file": 1}, "error": None})
    assert ok2["completed"] is True
    failed = flag_empty_completion(
        {"completed": False, "final_text": "", "tool_stats": {}, "error": "timeout after 1s"})
    assert failed["error"] == "timeout after 1s"


def test_event_to_stream_line_serializable_tool_input():
    line = event_to_stream_line({
        "event": "on_tool_start", "name": "search_memory", "run_id": "t9",
        "data": {"input": {"query": "婚礼", "limit": 8}},
    })
    assert json.loads(json.dumps(line))["data"]["input"] == {"query": "婚礼", "limit": 8}
    # 无 usage 的 model_end、无关事件不产出行
    assert event_to_stream_line({"event": "on_chat_model_end", "data": {"output": None}}) is None
    assert event_to_stream_line({"type": "unknown"}) is None


def test_final_text_takes_last_message_not_narration():
    """终稿只取最后一个完整回合：中间回合的过程叙述不得混进判分输入。"""
    collector = StreamCollector()
    _feed(
        collector,
        {"event": "on_chat_model_stream", "data": {"chunk": type("C", (), {"content": "先建任务清单，并行委派检索。"})()}},
        {"event": "on_chat_model_end", "data": {
            "output": type("O", (), {"usage_metadata": {"input_tokens": 10, "output_tokens": 2}})()}},
        {"event": "on_tool_start", "name": "start_task", "run_id": "t1", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "start_task", "run_id": "t1", "data": {"output": "ok"}},
        {"event": "on_chat_model_stream", "data": {"chunk": type("C", (), {"content": "# 调研报告\n\n正文…"})()}},
        {"event": "on_chat_model_end", "data": {
            "output": type("O", (), {"usage_metadata": {"input_tokens": 20, "output_tokens": 5}})()}},
        {"type": "__tw_finish__", "finish_reason": "stop"},
    )
    record = collector.to_record()
    assert record["final_text"].startswith("# 调研报告")
    assert "先建任务清单" not in record["final_text"]
    # 全量文本仍可查（过程审计用）
    assert "先建任务清单" in record["all_text"]
