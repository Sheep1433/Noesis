"""HITL SSE：interrupt 提取与 bridge hitl-required。"""

from __future__ import annotations

from types import SimpleNamespace

from domain.chat.streaming.hitl import build_hitl_required_event, extract_interrupt_payload, resolve_hitl_kind
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge


def test_extract_interrupt_from_on_chain_stream() -> None:
    interrupt = SimpleNamespace(
        id="intr-1",
        value={
            "action_requests": [{"name": "execute", "args": {"command": "curl x"}, "description": "d"}],
            "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
        },
    )
    event = {
        "event": "on_chain_stream",
        "data": {"chunk": {"__interrupt__": (interrupt,)}},
    }
    got = extract_interrupt_payload(event)
    assert got is not None
    assert got[0] == "intr-1"


def test_build_hitl_required_enriches_tool_call_id() -> None:
    value = {
        "action_requests": [{"name": "execute", "args": {"command": "curl x"}}],
        "review_configs": [{"action_name": "execute", "allowed_decisions": ["approve", "reject"]}],
    }
    ev = build_hitl_required_event(
        interrupt_id="i1",
        hitl_value=value,
        session_id="s1",
        message_id="m1",
        tool_calls=[{"id": "call_abc", "name": "execute", "args": {"command": "curl x"}}],
    )
    assert ev["kind"] == "approval"
    assert ev["action_requests"][0]["tool_call_id"] == "call_abc"
    assert resolve_hitl_kind([{"name": "ask_user"}]) == "clarification"


def test_bridge_emits_hitl_required_and_tool_parts() -> None:
    bridge = LangGraphSseBridge("s1", assistant_message_id="aid-1")
    builder = AssistantMessageBuilder(session_id="s1", message_id="aid-1")
    ctx: dict = {}
    payload = {
        "type": "hitl-required",
        "interrupt_id": "i1",
        "kind": "approval",
        "action_requests": [
            {
                "name": "execute",
                "args": {"command": "curl https://example.com"},
                "tool_call_id": "call_1",
                "description": "net",
            }
        ],
        "review_configs": [],
        "expires_at": 1,
    }
    lines = bridge.process_item(payload, builder, ctx)
    joined = "".join(lines)
    assert "hitl-required" in joined
    assert "tool-input-available" in joined
    assert bridge.last_hitl_payload is not None
    assert builder.to_dict()["parts"]
    assert builder.to_dict()["parts"][0]["hitl"]["status"] == "pending"

    finish_lines = bridge.process_item(
        {"type": "__tw_finish__", "finish_reason": "hitl_pending"},
        builder,
        ctx,
    )
    assert any("hitl_pending" in line for line in finish_lines)
    assert bridge.last_finish_reason == "hitl_pending"
