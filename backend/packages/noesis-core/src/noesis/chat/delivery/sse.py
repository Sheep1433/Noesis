"""SSE 编解码、总线订阅与 LC→RunEvent 映射。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional, TYPE_CHECKING

from noesis.chat.delivery.bus import (
    RunEventBus,
    bus_error_exc,
    is_bus_end,
    is_bus_error,
)
from noesis.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunEvent,
    RunPaused,
    RunSnapshotReplaced,
    StreamDone,
    WireFrame,
)

SSE_COMMENT_KEEPALIVE = b": keepalive\n\n"

if TYPE_CHECKING:
    from noesis.chat.runs import SequencedRunEvent


def format_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_sse_bytes(event: str, data: Dict[str, Any]) -> bytes:
    """编码一次、扇出共享的发布点产物：bytes 不可变，多订阅队列共享引用。"""
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


def format_done() -> str:
    return "data: [DONE]\n\n"


def format_done_bytes() -> bytes:
    return b"data: [DONE]\n\n"


def encode_run_event(event: RunEvent) -> List[tuple]:
    """将单个 RunEvent 编码为 0..n 条 (event_name, payload_dict) 对。

    单次计算双产物：payload_dict 供 bus/hub wire 与 bytes 编码共用——
    同一事件不再二次序列化（发布点经 ``format_sse_bytes`` 得到可共享的
    bytes 产物）。``("__done__", {})`` 是 StreamDone 的本地哨兵对，
    仅 ``encode_sequenced_event`` 消费为 [DONE] 字节。

    终态词汇统一：RunCompleted / RunAborted / RunError 均编码为
    ``run.finished``（唯一流终止标记，载荷含 status / finish_reason /
    usage / model_calls）；RunPaused(hitl_pending) 为非终态，走 run-status。
    """
    if isinstance(event, StreamDone):
        return [("__done__", {})]

    if isinstance(event, RunSnapshotReplaced):
        payload = dict(event.payload)
        payload.setdefault("type", "run-snapshot")
        return [("run-snapshot", payload)]

    if isinstance(event, WireFrame):
        return [(event.event, event.data)]

    if isinstance(event, HitlRequired):
        payload = dict(event.payload)
        payload.setdefault("type", "hitl-required")
        return [("hitl-required", payload)]

    if isinstance(event, RunPaused):
        data: Dict[str, Any] = {
            "type": "run-status",
            "status": "hitl_pending",
            "finish_reason": event.finish_reason or event.reason,
            "usage": event.usage or {},
        }
        if event.model_calls:
            data["model_calls"] = event.model_calls
        return [("run-status", data)]

    if isinstance(event, RunCompleted):
        data = {
            "type": "run.finished",
            "status": "completed",
            "finish_reason": event.finish_reason or "stop",
            "usage": event.usage or {},
        }
        if event.model_calls:
            data["model_calls"] = event.model_calls
        return [("run.finished", data)]

    if isinstance(event, RunAborted):
        return [
            (
                "run.finished",
                {
                    "type": "run.finished",
                    "status": "interrupted",
                    "finish_reason": event.reason,
                    "usage": {},
                },
            )
        ]

    if isinstance(event, RunError):
        return [
            (
                "run.finished",
                {
                    "type": "run.finished",
                    "status": "error",
                    "error": event.message,
                    "finish_reason": event.finish_reason or "error",
                    "usage": {},
                },
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

    if event_name == "run-status":
        # hitl_pending 分段结束：非终态，usage 随载荷保留
        if str(data.get("status") or "") == "hitl_pending":
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            model_calls = (
                data.get("model_calls") if isinstance(data.get("model_calls"), list) else []
            )
            return [
                RunPaused(
                    reason="hitl_pending",
                    finish_reason="hitl_pending",
                    usage=usage,
                    model_calls=model_calls,
                )
            ]
        return []

    if event_name == "run.finished":
        reason = str(data.get("finish_reason") or "stop")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        model_calls = (
            data.get("model_calls") if isinstance(data.get("model_calls"), list) else []
        )
        status = str(data.get("status") or "completed")
        if status == "error":
            return [
                RunError(
                    message=str(data.get("error") or "生成失败，请稍后重试"),
                    finish_reason=reason,
                )
            ]
        if status == "interrupted":
            return [RunAborted(reason=reason)]
        return [RunCompleted(finish_reason=reason, usage=usage, model_calls=model_calls)]

    return [WireFrame(event=event_name, data=data)]


def should_encode_for_sse(event: RunEvent) -> bool:
    return isinstance(
        event,
        (
            WireFrame,
            HitlRequired,
            RunPaused,
            RunCompleted,
            RunAborted,
            RunError,
            RunSnapshotReplaced,
            StreamDone,
        ),
    )


def encode_filtered(event: RunEvent) -> list[tuple]:
    if not should_encode_for_sse(event):
        return []
    return encode_run_event(event)


def sequenced_event_payloads(envelope: "SequencedRunEvent") -> List[tuple]:
    """(event_name, payload) 列表——SSE 编码与 Run bus 广播共用的 wire 形状。

    单次计算：由 ``encode_run_event`` 的 (event_name, payload_dict) 对
    直接注入 sequence / attempt_id / run_id，不再经 str 往返二次解析。
    StreamDone 是本地流终止标记（无 wire 载荷），不产生 bus 载荷。
    """
    if isinstance(envelope.event, StreamDone):
        return []
    payloads: List[tuple] = []
    for event_name, payload in encode_filtered(envelope.event):
        payload = dict(payload)
        payload["run_id"] = envelope.run_id
        payload["sequence"] = envelope.sequence
        payload["attempt_id"] = envelope.attempt_id
        payloads.append((event_name, payload))
    return payloads


def encode_sequenced_event(envelope: "SequencedRunEvent") -> list[bytes]:
    """发布点唯一编码入口：一次编码产出 bytes，扇出路径共享同一引用。

    与 ``sequenced_event_payloads`` 共享同一次事件级计算（(name, payload)
    对），消费侧（SSE 生成器 / hub / CLI）拿到 bytes 纯转发即可。
    """
    if isinstance(envelope.event, StreamDone):
        return [format_done_bytes()]
    return [
        format_sse_bytes(event_name, payload)
        for event_name, payload in sequenced_event_payloads(envelope)
    ]


async def iter_sse_from_bus(
    bus: RunEventBus,
    run_id: str,
    *,
    keepalive_seconds: float = 0.0,
    queue: Optional[asyncio.Queue[Any]] = None,
) -> AsyncGenerator[bytes, None]:
    """
    订阅总线并产出 SSE bytes（与发布点共享编码产物同族的字节形态）。

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
                for event_name, payload in encode_filtered(item):
                    yield format_sse_bytes(event_name, payload)

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
            for event_name, payload in encode_filtered(item):
                yield format_sse_bytes(event_name, payload)
    finally:
        if own:
            bus.unsubscribe_queue(run_id, q)
