"""message_builder 落库格式。"""

from domain.chat.message_builder import AssistantMessageBuilder, ToolPart


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
