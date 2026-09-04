"""RuntimeEventMapper：LangGraph/LangChain raw event → typed RunEvent 的唯一映射入口。

它是无状态模块（Bridge 实例有状态，mapper 本身不持有状态），不做 plugin registry
或深类层级。Web、Channel、cron 和 eval 中属于本 change 范围的 Agent Run 共用此 mapper。

状态提取器直接产出 typed RunEvent；SSE 字符串只由 SseDelivery 编码。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from noesis.chat.delivery.events import (
    HitlRequired,
    RunAborted,
    RunCompleted,
    RunError,
    RunEvent,
    RunPaused,
    WireFrame,
)
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge


def new_stream_ctx() -> Dict[str, Any]:
    """astream_events 消费的桥接上下文（主链路与子 Agent executor 共用）。"""
    return {
        "text_buffer": "",
        "current_tool_name": None,
        "current_tool_call_id": None,
        "tool_start_times": {},
        "_assistant_db_id": None,
        # 并行工具分组：按 scope（parent_task_call_id 或 "root"）独立计数 step_id。
        # on_chat_model_start 标 pending scope，首个 on_tool_start mint 新 step_id。
        "pending_model_step_scopes": set(),
        "step_counters": {},
        "current_step_ids": {},
    }


class RuntimeEventMapper:
    """raw runtime event → typed RunEvent 的唯一映射入口。

    每个 Run 持有独立 mapper；它只负责 raw event 到 typed event 的映射。
    """

    def __init__(self, bridge: LangGraphSseBridge) -> None:
        self.bridge = bridge

    def map_item(
        self,
        item: Dict[str, Any],
        builder: Optional[AssistantMessageBuilder],
        ctx: Dict[str, Any],
    ) -> List[RunEvent]:
        """单条 raw event → 多条 typed RunEvent。"""
        return self._normalize(self.bridge.map_item(item, builder, ctx))

    def finalize(self, *, finish_reason: Optional[str] = None) -> List[RunEvent]:
        """流结束：产出 finish + StreamDone。"""
        return self._normalize(self.bridge.finalize_events(finish_reason=finish_reason))

    @staticmethod
    def _normalize(events: List[RunEvent]) -> List[RunEvent]:
        normalized: List[RunEvent] = []
        for event in events:
            if not isinstance(event, WireFrame):
                normalized.append(event)
                continue
            data = event.data
            if event.event == "hitl-required":
                normalized.append(HitlRequired(payload=dict(data)))
            elif event.event == "finish":
                reason = str(data.get("finish_reason") or "stop")
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                model_calls = (
                    data.get("model_calls") if isinstance(data.get("model_calls"), list) else []
                )
                if reason == "hitl_pending":
                    normalized.append(
                        RunPaused(
                            reason="hitl_pending",
                            finish_reason=reason,
                            usage=usage,
                            model_calls=model_calls,
                        )
                    )
                # length_stop / safety_stop 走 completed 分支：输出与 usage 完整
                # （只是最后一步被 provider 截断/安全收尾），且客户端契约里这两
                # 个值随 finish 帧以成功形态结算——转 RunAborted 会发 abort 帧，
                # 客户端没有服务端主动 abort 的处理器，流会悬在不结束状态。
                elif reason in {
                    "partial_output",
                    "empty_after_tools",
                    "tool_loop_limit",
                    "tool_call_limit",
                    "subagent_concurrency_limit",
                    "subagent_total_limit",
                    "subagent_depth_limit",
                    "stopped",
                }:
                    normalized.append(RunAborted(reason=reason))
                elif reason in {"error", "context_exhausted", "retryable_error"}:
                    normalized.append(
                        RunError(
                            message="生成失败，请稍后重试",
                            finish_reason=reason,
                        )
                    )
                else:
                    normalized.append(
                        RunCompleted(
                            finish_reason=reason,
                            usage=usage,
                            model_calls=model_calls,
                        )
                    )
            elif event.event == "abort":
                normalized.append(
                    RunAborted(reason=str(data.get("reason") or "abort"))
                )
            elif event.event == "error":
                normalized.append(
                    RunError(
                        message=str(data.get("error") or data.get("content") or "error"),
                        finish_reason=str(data.get("finish_reason") or "error"),
                    )
                )
            else:
                normalized.append(event)
        return normalized
