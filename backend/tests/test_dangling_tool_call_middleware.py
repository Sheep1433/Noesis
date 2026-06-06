from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware


def _ai_with_tool_calls(tool_calls):
    return AIMessage(content="", tool_calls=tool_calls)


def _tool_msg(tool_call_id, name="test_tool"):
    return ToolMessage(content="result", tool_call_id=tool_call_id, name=name)


def _tc(name="bash", tc_id="call_1"):
    return {"name": name, "id": tc_id, "args": {}}


def test_single_dangling_call_is_patched():
    mw = DanglingToolCallMiddleware()
    msgs = [_ai_with_tool_calls([_tc("bash", "call_1")])]
    patched = mw._build_patched_messages(msgs)
    assert patched is not None
    assert len(patched) == 2
    assert isinstance(patched[1], ToolMessage)
    assert patched[1].tool_call_id == "call_1"
    assert patched[1].status == "error"


def test_raw_provider_tool_calls_are_patched():
    mw = DanglingToolCallMiddleware()
    msgs = [
        AIMessage(
            content="",
            tool_calls=[],
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                    }
                ]
            },
        )
    ]
    patched = mw._build_patched_messages(msgs)
    assert patched is not None
    assert len(patched) == 2
    assert isinstance(patched[1], ToolMessage)
    assert patched[1].tool_call_id == "call_1"
    assert patched[1].name == "bash"


def test_wrap_model_call_patched_request_forwarded():
    mw = DanglingToolCallMiddleware()
    request = MagicMock()
    request.messages = [_ai_with_tool_calls([_tc("bash", "call_1")])]
    patched_request = MagicMock()
    request.override.return_value = patched_request
    handler = MagicMock(return_value="response")

    result = mw.wrap_model_call(request, handler)

    request.override.assert_called_once()
    passed_messages = request.override.call_args.kwargs["messages"]
    assert len(passed_messages) == 2
    assert isinstance(passed_messages[1], ToolMessage)
    handler.assert_called_once_with(patched_request)
    assert result == "response"


@pytest.mark.anyio
async def test_awrap_model_call_patched_request_forwarded():
    mw = DanglingToolCallMiddleware()
    request = MagicMock()
    request.messages = [_ai_with_tool_calls([_tc("bash", "call_1")])]
    patched_request = MagicMock()
    request.override.return_value = patched_request
    handler = AsyncMock(return_value="response")

    result = await mw.awrap_model_call(request, handler)

    request.override.assert_called_once()
    passed_messages = request.override.call_args.kwargs["messages"]
    assert len(passed_messages) == 2
    assert isinstance(passed_messages[1], ToolMessage)
    handler.assert_called_once_with(patched_request)
    assert result == "response"


def test_all_tool_calls_responded_returns_none():
    mw = DanglingToolCallMiddleware()
    msgs = [
        HumanMessage(content="hi"),
        _ai_with_tool_calls([_tc("bash", "call_1")]),
        _tool_msg("call_1", "bash"),
    ]
    assert mw._build_patched_messages(msgs) is None
