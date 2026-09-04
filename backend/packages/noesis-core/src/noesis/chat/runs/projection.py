"""RunProjection：从 RunEvent 序列 reduce 出的当前 UI/消息状态。

属于 chat 子系统的领域语义——projection 规则是状态机的一部分，
不涉及 HTTP、ORM 或 service 编排。
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from noesis.runtime.logging import logger
from noesis.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunEvent,
    RunPaused,
    StreamDone,
    WireFrame,
)
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.tool_state import ToolState
from noesis.chat.runs.models import RunSnapshot, RunStatus


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class RunProjection:
    run_id: str
    user_id: str
    session_id: str
    assistant_message_id: str
    qa_type: str
    origin: str = "web"
    status: RunStatus = RunStatus.RUNNING
    attempt_id: int = 1
    finish_reason: str | None = None
    error_code: str | None = None
    user_error_message: str | None = None
    cancel_requested: bool = False
    pending_hitl: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.builder = AssistantMessageBuilder(
            session_id=self.session_id, message_id=self.assistant_message_id
        )
        # 本 run 的 usage 聚合（RunCompleted.usage 捕获）；每 run 独立，不随 clone/snapshot 持久化
        self.run_usage: dict[str, Any] | None = None
        # 本 run 的每次模型调用明细（RunCompleted.model_calls 捕获）；
        # 与 run_usage 同生命周期，终态落 message.extra.model_calls
        self.run_model_calls: list[dict[str, Any]] | None = None

    def clone(self) -> "RunProjection":
        """复制 projection 数据，不复制 builder 内部的线程锁。"""
        cloned = RunProjection(
            run_id=self.run_id,
            user_id=self.user_id,
            session_id=self.session_id,
            assistant_message_id=self.assistant_message_id,
            qa_type=self.qa_type,
            origin=self.origin,
            status=self.status,
            attempt_id=self.attempt_id,
            finish_reason=self.finish_reason,
            error_code=self.error_code,
            user_error_message=self.user_error_message,
            cancel_requested=self.cancel_requested,
            pending_hitl=copy.deepcopy(self.pending_hitl),
        )
        cloned.builder.load_from_content_dict(copy.deepcopy(self.builder.to_dict()))
        return cloned

    def apply(self, event: RunEvent, *, attempt_id: int | None = None) -> bool:
        if attempt_id is not None and attempt_id != self.attempt_id:
            logger.warning(
                "忽略旧 attempt 事件 run_id={} event_attempt={} current_attempt={}",
                self.run_id,
                attempt_id,
                self.attempt_id,
            )
            return False
        if self.status in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.ERROR,
            RunStatus.INTERRUPTED,
        } and isinstance(event, (RunCompleted, RunAborted, RunError)):
            logger.warning(
                "忽略终态 run 的迟到终态事件 run_id={} status={} event={}",
                self.run_id,
                self.status.value,
                type(event).__name__,
            )
            return False
        # LangGraph 每个执行分段都会发 [DONE]。HITL pause 后仍属于同一个 Run，
        # 不能把分段结束投递成整个订阅结束。
        if isinstance(event, StreamDone):
            return self.status != RunStatus.HITL_PENDING
        if isinstance(event, WireFrame):
            data = event.data
            if event.event == "text-delta":
                delta = data.get("text_delta") or data.get("delta") or data.get("content")
                self.builder.append_text_delta(
                    str(delta or ""),
                    parent_task_call_id=data.get("parent_task_call_id"),
                    part_id=str(data.get("part_id") or "") or None,
                )
            elif event.event == "reasoning-delta":
                delta = data.get("text_delta") or data.get("delta") or data.get("content")
                self.builder.append_reasoning_delta(
                    str(delta or ""),
                    parent_task_call_id=data.get("parent_task_call_id"),
                )
            elif event.event == "stream-rollback":
                # LLM 重试/降级：失败尝试的部分流式输出不进落库投影
                self.builder.rollback_trailing_stream_parts()
            elif event.event in {"tool-call-start", "tool-input-start"}:
                if event.event == "tool-input-start":
                    return True
                self.builder.append_tool(
                    str(data.get("tool_name") or data.get("name") or "tool"),
                    data.get("input") if isinstance(data.get("input"), dict) else {},
                    str(data.get("tool_call_id") or ""),
                    data.get("parent_task_call_id"),
                    state=data.get("state") or ToolState.RUNNING,
                )
            elif event.event == "tool-input-available":
                self.builder.append_tool(
                    str(data.get("tool_name") or data.get("name") or "tool"),
                    data.get("input") if isinstance(data.get("input"), dict) else {},
                    str(data.get("tool_call_id") or ""),
                    data.get("parent_task_call_id"),
                    state=data.get("state") or ToolState.RUNNING,
                )
            elif event.event == "tool-output-available":
                if self.cancel_requested or self.status in {
                    RunStatus.COMPLETED,
                    RunStatus.PARTIAL,
                    RunStatus.ERROR,
                    RunStatus.INTERRUPTED,
                }:
                    logger.warning(
                        "忽略 run 终止后的工具结果 run_id={} tool_call_id={}",
                        self.run_id,
                        data.get("tool_call_id"),
                    )
                    return
                try:
                    self.builder.append_tool_output(
                        str(data.get("tool_name") or data.get("name") or "tool"),
                        str(data.get("output") or ""),
                        str(data.get("tool_call_id") or ""),
                        status=str(data.get("status") or "success"),
                        state=data.get("state"),
                        error=str(data.get("error")) if data.get("error") else None,
                        error_category=(
                            str(data.get("errorCategory")) if data.get("errorCategory") else None
                        ),
                        outcome=str(data.get("outcome")) if data.get("outcome") else None,
                        exit_code=(int(data["exit_code"]) if data.get("exit_code") is not None else None),
                        timed_out=(bool(data["timed_out"]) if data.get("timed_out") is not None else None),
                        truncated=(bool(data["truncated"]) if data.get("truncated") is not None else None),
                        duration_ms=(int(data["duration_ms"]) if data.get("duration_ms") is not None else None),
                    )
                except ValueError:
                    logger.warning(
                        "忽略无匹配 tool start 的 run 投影结果 run_id={} tool_call_id={}",
                        self.run_id,
                        data.get("tool_call_id"),
                    )
            elif event.event == "retrieval-results-available":
                results = data.get("results")
                if isinstance(results, list):
                    origin = data.get("origin")
                    tool_call_id = str(data.get("tool_call_id") or "")
                    part = self.builder.register_retrieval_results(
                        tool_call_id=tool_call_id,
                        query=str(data.get("query") or ""),
                        results=[item for item in results if isinstance(item, dict)],
                        truncated=bool(data.get("truncated")),
                        origin=origin if isinstance(origin, dict) else None,
                    )
                    # 与 register_tool_retrieval 的共享投影语义对齐：tool part
                    # 展示输出替换为「检索到 N 条来源」摘要，原始结果只持久化
                    # 在 retrieval part——主链路重放此前绕过该替换，导致主/
                    # 子会话 tool part 数据形态不一致（主会话存了整份 JSON）。
                    tool_part = self.builder.get_tool(tool_call_id)
                    if tool_part is not None:
                        tool_part.output = f"检索到 {len(part.results)} 条来源"
            elif event.event == "run-status":
                status = str(data.get("status") or "")
                if status == "retrying":
                    # retrying 是单次模型调用的重试瞬态，不是 run 生命周期
                    # 状态：写进 self.status 会在恢复后钉死到终态交接（终态
                    # 事件只应用在 clone 上），且终态保留窗口内 GET 永远报
                    # retrying。帧照常放行 fan-out，前端重试提示不受影响。
                    pass
                elif status in {item.value for item in RunStatus}:
                    self.status = RunStatus(status)
        elif isinstance(event, HitlRequired):
            self.status = RunStatus.HITL_PENDING
            self.pending_hitl = dict(event.payload)
            for action in event.payload.get("action_requests") or []:
                if not isinstance(action, dict):
                    continue
                tool_call_id = str(action.get("tool_call_id") or "")
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {
                        "kind": event.payload.get("kind") or "approval",
                        "status": "pending",
                        "interrupt_id": event.payload.get("interrupt_id"),
                    },
                    status="running",
                    state=ToolState.APPROVAL_PENDING,
                )
            keep = {
                str(action.get("tool_call_id") or "")
                for action in event.payload.get("action_requests") or []
                if isinstance(action, dict) and action.get("tool_call_id")
            }
            self.builder.reconcile_nonterminal_tools(
                ToolState.CANCELLED,
                "本次工具执行已停止",
                keep_approval_call_ids=keep,
            )
        elif isinstance(event, RunPaused):
            self.status = RunStatus.HITL_PENDING
            self.finish_reason = event.finish_reason or event.reason
        elif isinstance(event, RunCompleted):
            self.builder.reconcile_nonterminal_tools(ToolState.CANCELLED, "本次工具执行已停止")
            self.status = RunStatus.COMPLETED
            self.finish_reason = event.finish_reason
            self.pending_hitl = None
            # 本条 assistant 消息的 usage 聚合（finish 事件携带），
            # 终态落库写入 message.extra.usage 供历史会话回放。
            if event.usage and event.usage.get("steps"):
                self.run_usage = dict(event.usage)
            if event.model_calls:
                self.run_model_calls = list(event.model_calls)
        elif isinstance(event, RunAborted):
            self.builder.reconcile_nonterminal_tools(ToolState.CANCELLED, "本次工具执行已停止")
            self.status = RunStatus.PARTIAL
            self.finish_reason = event.reason
            self.error_code = event.error_code
            self.user_error_message = event.message
        elif isinstance(event, RunError):
            terminal_state = (
                ToolState.TIMED_OUT
                if event.finish_reason in {"timeout", "hitl_timeout", "run_timeout"}
                else ToolState.FAILED
            )
            self.builder.reconcile_nonterminal_tools(terminal_state, event.message)
            self.status = RunStatus.ERROR
            self.finish_reason = event.finish_reason
            self.error_code = event.error_code
            self.user_error_message = event.message
        return True

    def begin_hitl_resume(self) -> None:
        if self.status != RunStatus.HITL_PENDING:
            raise ValueError("run is not waiting for HITL")
        self.status = RunStatus.RUNNING
        self.pending_hitl = None

    def apply_hitl_decisions(self, decisions: list[dict[str, Any]]) -> None:
        payload = self.pending_hitl or {}
        actions = payload.get("action_requests") or []
        for index, decision in enumerate(decisions):
            action = actions[index] if index < len(actions) and isinstance(actions[index], dict) else {}
            tool_call_id = action.get("tool_call_id")
            decision_type = str(decision.get("type") or "")
            if decision_type == "approve":
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "approved", "decision": "approve"},
                    status="running",
                    state=ToolState.RUNNING,
                )
            elif decision_type == "reject":
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "rejected", "decision": "reject"},
                    status="error",
                    state=ToolState.REJECTED,
                )
            elif decision_type == "respond":
                self.builder.update_tool_hitl(
                    tool_call_id,
                    {"status": "answered", "decision": "respond"},
                    status="success",
                    state=ToolState.SUCCEEDED,
                )

    def snapshot(self, sequence: int, status: RunStatus, attempt_id: int) -> RunSnapshot:
        effective_status = self.status if self.status != RunStatus.RUNNING else status
        return RunSnapshot(
            run_id=self.run_id,
            user_id=self.user_id,
            session_id=self.session_id,
            assistant_message_id=self.assistant_message_id,
            qa_type=self.qa_type,
            origin=self.origin,
            status=effective_status,
            sequence=sequence,
            attempt_id=attempt_id,
            parts=tuple(self.builder.to_public_dict().get("parts", [])),
            finish_reason=self.finish_reason,
            error_code=self.error_code,
            user_error_message=self.user_error_message,
            pending_hitl=self.pending_hitl,
            updated_at=_now_ms(),
        )

    def persisted_snapshot(self) -> dict[str, Any]:
        snapshot = self.builder.to_dict()
        if self.pending_hitl is not None:
            snapshot["_pending_hitl"] = dict(self.pending_hitl)
        return snapshot
