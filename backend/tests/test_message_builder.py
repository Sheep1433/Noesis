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
