import copy
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.middlewares.loop_detection_middleware import (
    _HARD_STOP_MSG,
    _WARNING_MSG,
    LoopDetectionMiddleware,
    _hash_tool_calls,
)


def _make_runtime(thread_id="test-thread"):
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id}
    return runtime


def _make_state(tool_calls=None, content=""):
    safe_content = copy.deepcopy(content) if isinstance(content, list) else content
    msg = AIMessage(content=safe_content, tool_calls=tool_calls or [])
    return {"messages": [msg]}


def _bash_call(cmd="ls"):
    return {"name": "bash", "id": f"call_{cmd}", "args": {"command": cmd}}


def test_hash_tool_calls_order_independent():
    a = _hash_tool_calls([_bash_call("ls"), {"name": "read_file", "args": {"path": "/tmp"}}])
    b = _hash_tool_calls([{"name": "read_file", "args": {"path": "/tmp"}}, _bash_call("ls")])
    assert a == b


def test_warn_queues_injection():
    mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=5)
    runtime = _make_runtime()
    call = [_bash_call("ls")]
    for _ in range(2):
        assert mw._apply(_make_state(tool_calls=call), runtime) is None
    assert mw._apply(_make_state(tool_calls=call), runtime) is None

    pending = mw._drain_pending_warnings(runtime)
    assert len(pending) == 1
    assert _WARNING_MSG in pending[0]


def test_hard_stop_at_limit():
    mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
    runtime = _make_runtime()
    call = [_bash_call("ls")]
    for _ in range(3):
        mw._apply(_make_state(tool_calls=call), runtime)
    result = mw._apply(_make_state(tool_calls=call), runtime)
    assert result is not None
    msg = result["messages"][0]
    assert isinstance(msg, AIMessage)
    assert msg.tool_calls == []
    assert _HARD_STOP_MSG in str(msg.content)


def test_non_ai_message_ignored():
    mw = LoopDetectionMiddleware()
    runtime = _make_runtime()
    state = {"messages": [SystemMessage(content="hello")]}
    assert mw._apply(state, runtime) is None


def test_hard_stop_with_list_content():
    mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
    runtime = _make_runtime()
    call = [_bash_call("ls")]
    list_content = [
        {"type": "thinking", "text": "Let me think..."},
        {"type": "text", "text": "I'll run ls"},
    ]
    for _ in range(3):
        mw._apply(_make_state(tool_calls=call, content=list_content), runtime)
    result = mw._apply(_make_state(tool_calls=call, content=list_content), runtime)
    msg = result["messages"][0]
    assert isinstance(msg.content, list)
    assert len(msg.content) == 3


def test_reset_clears_state():
    mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=5)
    runtime = _make_runtime()
    call = [_bash_call("ls")]
    mw._apply(_make_state(tool_calls=call), runtime)
    mw._apply(_make_state(tool_calls=call), runtime)
    mw.reset()
    assert mw._apply(_make_state(tool_calls=call), runtime) is None
