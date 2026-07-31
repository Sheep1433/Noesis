"""message_builder 落库格式。"""

import json

from noesis_server.domain.chat.message_builder import AssistantMessageBuilder, ToolPart


def _evidence_result(**overrides):
    return {
        "collection_name": "requirements",
        "document_id": "doc_1",
        "document_version_id": "docv_1",
        "segment_id": "seg_1",
        "file_name": "登录需求.md",
        "excerpt": "验证码发送后 5 分钟内有效。",
        "locator": {"type": "page", "page_start": 3, "page_end": 3},
        "citable": True,
        **overrides,
    }


def test_tool_part_to_dict_snake_case_only() -> None:
    part = ToolPart(
        name="bash",
        arguments={"ip": "1.2.3.4"},
        output="ok",
        tool_call_id="call-1",
        duration_ms=100,
        status="success",
    )
    d = part.to_dict()
    assert d == {
        "type": "tool",
        "name": "bash",
        "input": {"ip": "1.2.3.4"},
        "output": "ok",
        "tool_call_id": "call-1",
        "status": "success",
        "state": "succeeded",
        "duration_ms": 100,
    }
    assert "toolCallId" not in d
    assert "toolName" not in d
    assert "arguments" not in d
    assert "durationMs" not in d


def test_append_text_delta_merges_same_parent() -> None:
    builder = AssistantMessageBuilder()
    builder.append_text_delta("你")
    builder.append_text_delta("好")
    builder.append_text_delta("！")
    parts = builder.to_dict()["parts"]
    assert len(parts) == 1
    assert parts[0]["type"] == "text"
    assert parts[0]["content"] == "你好！"


def test_retrieval_manifest_survives_snapshot_restore() -> None:
    original = AssistantMessageBuilder()
    retrieval = original.register_retrieval_results(
        tool_call_id="call-1",
        query="验证码",
        results=[_evidence_result()],
    )
    restored = AssistantMessageBuilder()
    restored.load_from_content_dict(original.to_dict())
    restored_retrieval = next(
        part for part in restored.to_dict()["parts"] if part["type"] == "retrieval"
    )
    assert restored_retrieval["results"] == retrieval.results


def test_replayed_retrieval_tool_output_updates_same_part() -> None:
    builder = AssistantMessageBuilder()
    first = builder.register_retrieval_results(
        tool_call_id="call-1", query="验证码", results=[_evidence_result()]
    )
    replay = builder.register_retrieval_results(
        tool_call_id="call-1", query="验证码", results=[_evidence_result()]
    )
    assert replay is first
    retrieval_parts = [part for part in builder.to_dict()["parts"] if part["type"] == "retrieval"]
    assert len(retrieval_parts) == 1
    assert len(retrieval_parts[0]["results"]) == 1


def test_retrieval_capacity_is_deterministic_and_utf8_safe() -> None:
    builder = AssistantMessageBuilder()
    results = [
        _evidence_result(
            segment_id=f"seg_{index}",
            excerpt="中" * 5000,
        )
        for index in range(25)
    ]
    retrieval = builder.register_retrieval_results(
        tool_call_id="call-1",
        query="容量",
        results=results,
    )
    assert len(retrieval.results) == 20
    assert retrieval.truncated is True
    assert len({item["evidence_id"] for item in retrieval.results}) == 20
    assert all(item["evidence_id"].startswith("ev_") for item in retrieval.results)
    assert all(len(item["excerpt"].encode("utf-8")) <= 8192 for item in retrieval.results)


def test_worst_case_retrieval_snapshot_stays_under_assistant_budget() -> None:
    builder = AssistantMessageBuilder()
    for call_index in range(5):
        builder.register_retrieval_results(
            tool_call_id=f"call-{call_index}",
            query="容量测试",
            results=[
                _evidence_result(
                    segment_id=f"seg_{call_index}_{index}",
                    excerpt="中" * 5000,
                    locator={"type": "header", "path": ["章" * 1500]},
                )
                for index in range(20)
            ],
        )
    payload = json.dumps(builder.to_dict(), ensure_ascii=False).encode("utf-8")
    assert len(payload) < 2 * 1024 * 1024
    assert len([
        part for part in builder.to_dict()["parts"] if part["type"] == "retrieval"
    ]) == 5


def test_append_text_delta_new_part_when_parent_changes() -> None:
    builder = AssistantMessageBuilder()
    builder.append_text_delta("主", parent_task_call_id=None)
    builder.append_text_delta("子", parent_task_call_id="task-1")
    parts = builder.to_dict()["parts"]
    assert len(parts) == 2
    assert parts[0]["content"] == "主"
    assert parts[1]["content"] == "子"
    assert parts[1]["parent_task_call_id"] == "task-1"


def test_append_reasoning_delta_merges_across_interleaved_parent() -> None:
    """主 Agent part 插入时，子 Agent reasoning 仍应合并为同一块。"""
    builder = AssistantMessageBuilder()
    builder.append_reasoning_delta("The", parent_task_call_id="task-1")
    builder.append_text_delta("主线", parent_task_call_id=None)
    builder.append_reasoning_delta(" user wants", parent_task_call_id="task-1")
    parts = builder.to_dict()["parts"]
    reasoning = [p for p in parts if p["type"] == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0]["content"] == "The user wants"
    assert reasoning[0]["parent_task_call_id"] == "task-1"


def test_text_and_reasoning_delta_after_retrieval_part_do_not_assume_parent_field() -> None:
    builder = AssistantMessageBuilder()
    builder.register_retrieval_results(
        tool_call_id="call-search",
        query="LLM wiki",
        results=[],
    )

    builder.append_reasoning_delta("继续分析")
    builder.append_text_delta("最终回答")

    parts = builder.to_dict()["parts"]
    assert [part["type"] for part in parts] == ["retrieval", "reasoning", "text"]
    assert parts[1]["content"] == "继续分析"
    assert parts[2]["content"] == "最终回答"


def test_append_tool_output_persists_error_status() -> None:
    builder = AssistantMessageBuilder()
    builder.append_tool("bash", {"command": "uptime"}, tool_call_id="tc-1")
    builder.append_tool_output(
        "bash",
        "工具执行环境不可用，请联系管理员检查 MCP 或沙箱配置。",
        "tc-1",
        duration_ms=42,
        status="error",
        error="工具执行环境不可用，请联系管理员检查 MCP 或沙箱配置。",
    )
    part = builder.to_dict()["parts"][0]
    assert part["status"] == "error"
    assert part["error"]
    assert part["duration_ms"] == 42


def test_replayed_tool_start_updates_same_part_instead_of_appending() -> None:
    builder = AssistantMessageBuilder()
    builder.append_tool("execute", {"command": "curl example.com"}, "call-1")
    builder.update_tool_hitl("call-1", {"status": "approved"})

    builder.append_tool("execute", {"command": "curl example.com"}, "call-1")
    builder.append_tool_output("execute", "ok", "call-1", status="success")

    parts = builder.to_dict()["parts"]
    assert len(parts) == 1
    assert parts[0]["tool_call_id"] == "call-1"
    assert parts[0]["status"] == "success"
    assert parts[0]["output"] == "ok"
    assert parts[0]["hitl"]["status"] == "approved"


def test_load_collapses_previously_persisted_duplicate_tool_parts() -> None:
    builder = AssistantMessageBuilder()
    builder.load_from_content_dict(
        {
            "parts": [
                {
                    "type": "tool",
                    "name": "execute",
                    "input": {"command": "curl example.com"},
                    "output": None,
                    "tool_call_id": "call-1",
                    "status": "running",
                    "hitl": {"status": "pending"},
                },
                {
                    "type": "tool",
                    "name": "execute",
                    "input": {"command": "curl example.com"},
                    "output": "ok",
                    "tool_call_id": "call-1",
                    "status": "success",
                    "hitl": {"status": "approved"},
                },
            ]
        }
    )

    parts = builder.to_dict()["parts"]
    assert len(parts) == 1
    assert parts[0]["status"] == "success"
    assert parts[0]["output"] == "ok"
    assert parts[0]["hitl"]["status"] == "approved"


def test_resolve_unique_hitl_tool_call_id_for_resume_callback() -> None:
    builder = AssistantMessageBuilder()
    builder.append_tool(
        "execute",
        {"command": "curl example.com"},
        "call-model-1",
        hitl={"status": "approved", "interrupt_id": "interrupt-1"},
    )

    assert builder.resolve_hitl_tool_call_id(
        "execute", {"command": "curl example.com"}
    ) == "call-model-1"


def test_running_tool_is_marked_unknown_when_cancel_cannot_be_confirmed() -> None:
    builder = AssistantMessageBuilder(session_id="s", message_id="m")
    builder.append_tool("remote_write", {"value": 1}, "call-1")

    assert builder.mark_running_tools_unknown("执行结果无法确认") == 1
    part = builder.to_dict()["parts"][0]
    assert part["status"] == "error"
    assert part["outcome"] == "unknown"
    assert part["errorCategory"] == "unknown"
