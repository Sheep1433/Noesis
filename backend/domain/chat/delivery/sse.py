"""SSE 编解码、总线订阅与 LC→RunEvent 映射。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from domain.chat.delivery.bus import (
    RunEventBus,
    bus_error_exc,
    is_bus_end,
    is_bus_error,
)
from domain.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunEvent,
    RunPaused,
    StreamDone,
    WireFrame,
)
from domain.chat.message_builder import AssistantMessageBuilder
from domain.chat.streaming.langgraph_sse import LangGraphSseBridge

SSE_COMMENT_KEEPALIVE = ": keepalive\n\n"


def format_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_done() -> str:
    return "data: [DONE]\n\n"


def encode_run_event(event: RunEvent) -> List[str]:
    """将单个 RunEvent 编码为 0..n 条 SSE 行。"""
    if isinstance(event, StreamDone):
        return [format_done()]

    if isinstance(event, WireFrame):
        return [format_sse(event.event, event.data)]

    if isinstance(event, HitlRequired):
        payload = dict(event.payload)
        payload.setdefault("type", "hitl-required")
        return [format_sse("hitl-required", payload)]

    if isinstance(event, RunPaused):
        data: Dict[str, Any] = {
            "type": "finish",
            "finish_reason": event.finish_reason or event.reason,
            "usage": event.usage or {},
        }
        if event.finish_reason:
            data["finish_reason"] = event.finish_reason
        return [format_sse("finish", data)]

    if isinstance(event, RunCompleted):
        return [
            format_sse(
                "finish",
                {
                    "type": "finish",
                    "finish_reason": event.finish_reason or "stop",
                    "usage": event.usage or {},
                },
            )
        ]

    if isinstance(event, RunAborted):
        return [format_sse("abort", {"type": "abort", "reason": event.reason})]

    if isinstance(event, RunError):
        return [
            format_sse(
                "error",
                {"type": "error", "error": event.message},
            )
        ]

    return []


def parse_sse_line_to_event(line: str) -> List[RunEvent]:
    """将现网 SSE 行解析回 RunEvent（供 LcEventMapper 包装 Bridge）。"""
    if not line:
        return []
    if line.startswith("data: [DONE]"):
        return [StreamDone()]
    if line.startswith(":"):
        return []

    event_name = ""
    data_raw = ""
    for part in line.strip().split("\n"):
        if part.startswith("event:"):
            event_name = part[len("event:") :].strip()
        elif part.startswith("data:"):
            data_raw = part[len("data:") :].strip()
    if not event_name or not data_raw:
        return []
    try:
        data = json.loads(data_raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []

    if event_name == "hitl-required":
        return [HitlRequired(payload=data)]

    if event_name == "finish":
        reason = str(data.get("finish_reason") or "stop")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        if reason == "hitl_pending":
            return [
                RunPaused(reason="hitl_pending", finish_reason=reason, usage=usage),
                WireFrame(event="finish", data=data),
            ]
        return [
            WireFrame(event="finish", data=data),
            RunCompleted(finish_reason=reason, usage=usage),
        ]

    if event_name == "abort":
        return [WireFrame(event="abort", data=data), RunAborted()]

    if event_name == "error":
        msg = str(data.get("error") or data.get("content") or "error")
        return [WireFrame(event="error", data=data), RunError(message=msg)]

    return [WireFrame(event=event_name, data=data)]


def should_encode_for_sse(event: RunEvent) -> bool:
    """Persist 专用事件（RunCompleted/RunPaused 等）不重复成帧。"""
    return isinstance(event, (WireFrame, HitlRequired, StreamDone))


def encode_filtered(event: RunEvent) -> list[str]:
    if not should_encode_for_sse(event):
        return []
    return encode_run_event(event)


async def iter_sse_from_bus(
    bus: RunEventBus,
    run_id: str,
    *,
    keepalive_seconds: float = 0.0,
    queue: Optional[asyncio.Queue[Any]] = None,
) -> AsyncGenerator[str, None]:
    """
    订阅总线并产出 SSE 字符串。

    ``keepalive_seconds > 0`` 时在空闲等待中注入注释帧；**不**向总线发布心跳。
    """
    own = queue is None
    q = queue or bus.subscribe_queue(run_id)
    try:
        if keepalive_seconds <= 0:
            while True:
                item = await q.get()
                if is_bus_end(item):
                    return
                if is_bus_error(item):
                    raise bus_error_exc(item)
                for line in encode_filtered(item):
                    yield line
            return

        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=keepalive_seconds)
            except asyncio.TimeoutError:
                yield SSE_COMMENT_KEEPALIVE
                continue
            if is_bus_end(item):
                return
            if is_bus_error(item):
                raise bus_error_exc(item)
            for line in encode_filtered(item):
                yield line
    finally:
        if own:
            bus.unsubscribe_queue(run_id, q)


class LcEventMapper:
    """
    将上游 raw dict 映射为 RunEvent。

    实现上复用 ``LangGraphSseBridge`` 的状态机与 builder 副作用，再把产出的 SSE 行
    解析为 typed RunEvent，保证与现网契约一致。
    """

    def __init__(self, bridge: LangGraphSseBridge) -> None:
        self.bridge = bridge

    def map_item(
        self,
        item: Dict[str, Any],
        builder: Optional[AssistantMessageBuilder],
        ctx: Dict[str, Any],
    ) -> List[RunEvent]:
        lines = self.bridge.process_item(item, builder, ctx)
        return self._lines_to_events(lines)

    def finalize(self, *, finish_reason: Optional[str] = None) -> List[RunEvent]:
        lines = self.bridge.finalize(finish_reason=finish_reason)
        return self._lines_to_events(lines)

    @staticmethod
    def _lines_to_events(lines: List[str]) -> List[RunEvent]:
        events: List[RunEvent] = []
        for line in lines:
            events.extend(parse_sse_line_to_event(line))
        return events
