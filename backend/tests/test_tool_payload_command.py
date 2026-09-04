"""tool_output_value 的 Command 解包。

start_task 等工具以 Command 返回（携带 bg_tasks 身份 update），模型可见文本
在 update.messages 的 ToolMessage 里；str(Command) repr 不是合法工具输出
（曾整段入库，前端也无法从中稳定提取子会话 id）。
"""

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from noesis.chat.event_mapping.tool_payload import tool_output_value


def test_command_output_unwraps_first_tool_message() -> None:
    raw = Command(update={
        "messages": [
            ToolMessage(
                content="任务运行超过 120s，已自动转为后台：c4d2a48e-295d-42a0-8057-dfa37717dd75",
                tool_call_id="call-1",
            ),
        ],
    })
    assert tool_output_value(raw) == (
        "任务运行超过 120s，已自动转为后台：c4d2a48e-295d-42a0-8057-dfa37717dd75"
    )


def test_command_output_without_messages_returns_empty() -> None:
    assert tool_output_value(Command(update={"bg_tasks": []})) == ""


def test_non_command_outputs_passthrough() -> None:
    assert tool_output_value(ToolMessage(content="ok", tool_call_id="c")) == "ok"
    assert tool_output_value("plain text") == "plain text"
    assert tool_output_value(None) == ""
