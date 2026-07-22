"""从 LangGraph astream_events 提取 HITL interrupt 并组装 SSE 载荷。"""

from __future__ import annotations

import time
from typing import Any, Optional

from config.env import HitlConfig


def extract_interrupt_payload(event: dict[str, Any]) -> Optional[tuple[str, Any]]:
    """若事件为带 ``__interrupt__`` 的 ``on_chain_stream``，返回 ``(interrupt_id, value)``。"""
    if event.get("event") != "on_chain_stream":
        return None
    chunk = (event.get("data") or {}).get("chunk")
    if not isinstance(chunk, dict):
        return None
    interrupts = chunk.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0] if isinstance(interrupts, (list, tuple)) else interrupts
    interrupt_id = getattr(first, "id", None) or (first.get("id") if isinstance(first, dict) else None)
    value = getattr(first, "value", None)
    if value is None and isinstance(first, dict):
        value = first.get("value")
    if not interrupt_id:
        return None
    return str(interrupt_id), value


def _tool_calls_from_model_end(event: dict[str, Any]) -> list[dict[str, Any]]:
    if event.get("event") != "on_chat_model_end":
        return []
    output = (event.get("data") or {}).get("output")
    raw = getattr(output, "tool_calls", None) or []
    out: list[dict[str, Any]] = []
    for tc in raw:
        if isinstance(tc, dict):
            out.append(tc)
        else:
            out.append(
                {
                    "id": getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None),
                    "name": getattr(tc, "name", None) or "",
                    "args": getattr(tc, "args", None) or {},
                }
            )
    return out


def enrich_action_requests(
    action_requests: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    remaining = list(tool_calls)
    enriched: list[dict[str, Any]] = []
    for ar in action_requests:
        name = ar.get("name")
        tool_call_id = ar.get("tool_call_id")
        if not tool_call_id:
            for i, tc in enumerate(remaining):
                if tc.get("name") == name:
                    tool_call_id = tc.get("id")
                    remaining.pop(i)
                    break
        item = dict(ar)
        if tool_call_id:
            item["tool_call_id"] = tool_call_id
        enriched.append(item)
    return enriched


def resolve_hitl_kind(action_requests: list[dict[str, Any]]) -> str:
    if action_requests and all(a.get("name") == "ask_user" for a in action_requests):
        return "clarification"
    return "approval"


def build_hitl_required_event(
    *,
    interrupt_id: str,
    hitl_value: Any,
    session_id: str,
    message_id: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_actions: list[dict[str, Any]] = []
    review_configs: list[dict[str, Any]] = []
    if isinstance(hitl_value, dict):
        raw_actions = list(hitl_value.get("action_requests") or [])
        review_configs = list(hitl_value.get("review_configs") or [])
    else:
        raw_actions = list(getattr(hitl_value, "action_requests", None) or [])
        review_configs = list(getattr(hitl_value, "review_configs", None) or [])

    actions = enrich_action_requests(
        [dict(a) for a in raw_actions],
        tool_calls or [],
    )
    expires_at = int(time.time()) + int(HitlConfig.ask_timeout_seconds)
    return {
        "type": "hitl-required",
        "interrupt_id": interrupt_id,
        "session_id": session_id,
        "message_id": message_id,
        "kind": resolve_hitl_kind(actions),
        "action_requests": actions,
        "review_configs": review_configs,
        "expires_at": expires_at,
    }
