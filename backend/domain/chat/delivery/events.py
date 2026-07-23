"""一次 Agent run 的内部事件语言（非 SSE 字符串）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Union

RunOrigin = Literal["web", "telegram", "wechat", "cron", "eval"]


@dataclass(frozen=True)
class RunStarted:
    run_id: str
    session_id: str
    assistant_message_id: str
    origin: RunOrigin = "web"


@dataclass(frozen=True)
class WireFrame:
    """与现网 SSE ``event`` / ``data`` 一一对应的投递事件（文本/工具/业务透传等）。"""

    event: str
    data: Dict[str, Any]


@dataclass(frozen=True)
class BusinessEvent:
    type: str
    payload: Dict[str, Any]


@dataclass(frozen=True)
class HitlRequired:
    """对齐现网 ``hitl-required`` 载荷。"""

    payload: Dict[str, Any]


@dataclass(frozen=True)
class RunPaused:
    """本段传输可结束；run 未终态（如 hitl_pending）。"""

    reason: str  # hitl_pending
    finish_reason: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunCompleted:
    finish_reason: str = "stop"
    usage: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunAborted:
    reason: str = "abort"


@dataclass(frozen=True)
class RunError:
    message: str
    finish_reason: str = "error"


@dataclass(frozen=True)
class StreamDone:
    """传输层收尾（对应 ``data: [DONE]``）；不表示业务终态。"""


RunEvent = Union[
    RunStarted,
    WireFrame,
    BusinessEvent,
    HitlRequired,
    RunPaused,
    RunCompleted,
    RunAborted,
    RunError,
    StreamDone,
]


def wire_frame(event: str, data: Optional[Dict[str, Any]] = None) -> WireFrame:
    payload = dict(data or {})
    payload.setdefault("type", event)
    return WireFrame(event=event, data=payload)
