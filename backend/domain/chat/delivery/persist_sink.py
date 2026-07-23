"""PersistSink：订阅 RunEvent，独占 assistant 落库状态机钩子。

具体 DB 写入仍委托 qa_service 中的既有 helper（过渡期），本类负责
「何时终态 / 何时 hitl_pending / 何时跳过」的判定，避免与传输生命周期缠死。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from domain.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunEvent,
    RunPaused,
)


@dataclass
class PersistDecision:
    """PersistSink 对单条或一批事件的落库建议。"""

    kind: str  # none | hitl_pending | completed | error | aborted
    finish_reason: str = ""
    error_message: str = ""
    hitl_payload: Optional[Dict[str, Any]] = None
    usage: Dict[str, Any] = field(default_factory=dict)


class PersistSink:
    """
    无状态收集器：消费 RunEvent，产出落库决策。

    - HitlRequired / RunPaused(hitl_pending) → **不**终态
    - RunCompleted / RunError / RunAborted → 终态
    - 无 SSE 订阅者时仍应在 run 结束后调用 apply 路径
    """

    def __init__(self) -> None:
        self.last_hitl: Optional[Dict[str, Any]] = None
        self.paused_hitl = False
        self.terminal: Optional[PersistDecision] = None
        self._seen_events: List[str] = []

    def on_event(self, event: RunEvent) -> Optional[PersistDecision]:
        name = type(event).__name__
        self._seen_events.append(name)

        if isinstance(event, HitlRequired):
            self.last_hitl = dict(event.payload)
            return None

        if isinstance(event, RunPaused):
            if event.reason == "hitl_pending" or event.finish_reason == "hitl_pending":
                self.paused_hitl = True
                return PersistDecision(
                    kind="hitl_pending",
                    finish_reason="hitl_pending",
                    hitl_payload=self.last_hitl,
                    usage=dict(event.usage or {}),
                )
            return None

        if isinstance(event, RunCompleted):
            if event.finish_reason == "hitl_pending":
                self.paused_hitl = True
                return PersistDecision(
                    kind="hitl_pending",
                    finish_reason="hitl_pending",
                    hitl_payload=self.last_hitl,
                    usage=dict(event.usage or {}),
                )
            # 若本段已 hitl pause，忽略随后误标的 completed（不应发生）
            if self.paused_hitl:
                return PersistDecision(
                    kind="hitl_pending",
                    finish_reason="hitl_pending",
                    hitl_payload=self.last_hitl,
                )
            decision = PersistDecision(
                kind="completed",
                finish_reason=event.finish_reason or "stop",
                usage=dict(event.usage or {}),
            )
            self.terminal = decision
            return decision

        if isinstance(event, RunError):
            decision = PersistDecision(
                kind="error",
                finish_reason=event.finish_reason or "error",
                error_message=event.message,
            )
            self.terminal = decision
            return decision

        if isinstance(event, RunAborted):
            decision = PersistDecision(kind="aborted", finish_reason="abort")
            self.terminal = decision
            return decision

        return None

    def final_decision(self) -> PersistDecision:
        if self.paused_hitl and (
            self.terminal is None or self.terminal.kind == "completed"
        ):
            return PersistDecision(
                kind="hitl_pending",
                finish_reason="hitl_pending",
                hitl_payload=self.last_hitl,
            )
        if self.terminal is not None:
            return self.terminal
        return PersistDecision(kind="completed", finish_reason="stop")
