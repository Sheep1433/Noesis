"""Agent run 状态机与对外快照 schema。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    HITL_PENDING = "hitl_pending"
    # 协作停止中间态（子 Agent run）：停止请求已受理，静止边界退出后转终态。
    # 仅存在于受理快照与事件流，不落库——DB 在终态事件到达前保持原状态。
    STOPPING = "stopping"
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"
    INTERRUPTED = "interrupted"


ACTIVE_RUN_STATUSES = frozenset(
    {
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RETRYING,
        RunStatus.HITL_PENDING,
    }
)

#: run 终态 → assistant 消息终态的权威映射（主/子链路落库共用单点；
#: interrupted 落 partial——中断的内容视作部分产出）。
ASSISTANT_TERMINAL_STATUS: dict[RunStatus, str] = {
    RunStatus.COMPLETED: "completed",
    RunStatus.PARTIAL: "partial",
    RunStatus.ERROR: "error",
    RunStatus.INTERRUPTED: "partial",
}
TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.PARTIAL,
        RunStatus.ERROR,
        RunStatus.INTERRUPTED,
    }
)

_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {RunStatus.RUNNING, RunStatus.PARTIAL, RunStatus.ERROR, RunStatus.INTERRUPTED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.RETRYING,
            RunStatus.HITL_PENDING,
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.ERROR,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.RETRYING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.PARTIAL,
            RunStatus.ERROR,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.HITL_PENDING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.PARTIAL,
            RunStatus.ERROR,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.PARTIAL: frozenset(),
    RunStatus.ERROR: frozenset(),
    RunStatus.INTERRUPTED: frozenset(),
}


class InvalidRunTransition(ValueError):
    """run 状态转换违反领域状态机。"""


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    """相同状态为幂等写；终态不能被其它状态覆盖。"""
    return current == target or target in _TRANSITIONS[current]


def require_transition(current: RunStatus, target: RunStatus) -> None:
    if not can_transition(current, target):
        raise InvalidRunTransition(f"invalid run transition: {current.value} -> {target.value}")


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    user_id: str
    session_id: str
    assistant_message_id: str
    qa_type: str
    origin: str
    status: RunStatus
    sequence: int = 0
    attempt_id: int = 1
    parts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    finish_reason: Optional[str] = None
    error_code: Optional[str] = None
    user_error_message: Optional[str] = None
    pending_hitl: Optional[dict[str, Any]] = None
    updated_at: Optional[int] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "assistant_message_id": self.assistant_message_id,
            "qa_type": self.qa_type,
            "origin": self.origin,
            "status": self.status.value,
            "snapshot_sequence": self.sequence,
            "attempt_id": self.attempt_id,
            "content": {"parts": [dict(part) for part in self.parts]},
            "finish_reason": self.finish_reason,
            "error_code": self.error_code,
            "message": self.user_error_message,
            "pending_hitl": self.pending_hitl,
            "updated_at": self.updated_at,
        }
