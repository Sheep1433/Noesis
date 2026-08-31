"""一次 Agent run 的内部事件语言（非 SSE 字符串）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

RunOrigin = Literal["web", "telegram", "wechat", "feishu", "cron", "eval", "cli"]


@dataclass(frozen=True)
class WireFrame:
    """与现网 SSE ``event`` / ``data`` 一一对应的投递事件（文本/工具/业务透传等）。"""

    event: str
    data: Dict[str, Any]
    # 内部并发身份，不进入 SSE payload。用于拒绝旧 model attempt 的迟到 delta。
    attempt_id: int | None = None


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
    # 中断前已发生的模型调用明细：resume 侧种子合并用（与 usage 同语义）
    model_calls: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RunCompleted:
    finish_reason: str = "stop"
    usage: Dict[str, Any] = field(default_factory=dict)
    # 每次模型调用一条明细（step/model/ttft/llm_ms/tokens/finish_reason），
    # 与 usage 同源下发，终态落 message.extra.model_calls。
    model_calls: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RunAborted:
    reason: str = "abort"
    error_code: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class RunError:
    message: str
    finish_reason: str = "error"
    error_code: Optional[str] = None


@dataclass(frozen=True)
class StreamDone:
    """传输层收尾（对应 ``data: [DONE]``）；不表示业务终态。"""


@dataclass(frozen=True)
class RunSnapshotReplaced:
    """terminal CAS loser 时交付数据库权威 snapshot。"""

    payload: Dict[str, Any]


RunEvent = Union[
    WireFrame,
    HitlRequired,
    RunPaused,
    RunCompleted,
    RunAborted,
    RunError,
    StreamDone,
    RunSnapshotReplaced,
]
