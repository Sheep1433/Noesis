"""message_builder 反序列化边角。"""
from domain.chat.message_builder import MessageContent


def test_tool_part_empty_input_dict_preserved() -> None:
    content = MessageContent.from_dict({
        "parts": [
            {
                "type": "tool",
                "name": "get_time",
                "input": {},
                "arguments": {"stale": "ignored"},
                "status": "success",
            },
        ],
    })
    part = content.parts[0]
    assert part.arguments == {}
