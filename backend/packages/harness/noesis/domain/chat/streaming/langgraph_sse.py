"""
LangGraph / LangChain astream_events → Noesis 标准 SSE，并同步累积 AssistantMessageBuilder。

仅保留 astream 原始事件 + 少量 __tw_* 控制哨兵（见 base_agent）。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from noesis.middlewares.observability.context_metrics_registry import ContextMetricsRegistry
from noesis.config.env import ModelConfig
from noesis.domain.chat.streaming.reasoning import (
    extract_reasoning_delta,
    extract_text_content,
    unsent_text_suffix,
)
from noesis.domain.chat.streaming.usage_normalize import (
    accumulate_detail as _accumulate_detail,
    extract_input_token_details as _extract_input_token_details,
    extract_output_token_details as _extract_output_token_details,
    normalize_usage as _normalize_usage,
    to_int as _to_int,
)
from noesis.domain.chat.streaming.usage_attribution import (
    CALLER_LEAD_AGENT,
    ModelCallAttribution,
    RunUsageCollector,
    resolve_caller,
)
from noesis.runtime.logging import logger
from noesis.domain.chat.message_builder import AssistantMessageBuilder
from noesis.domain.chat.tool_state import (
    ToolState,
    derive_tool_state,
    extract_process_result,
)
from noesis.domain.chat.streaming.bridge import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridgeError
from noesis.domain.chat.streaming.failure_notice import sanitize_stream_error, sanitize_tool_error
from noesis.errors.tool_failure import (
    ToolFailure,
    classify_task_tool_output,
    classify_tool_failure,
    failure_to_sse_error_fields,
    subagent_failure_from_context,
)


def _show_thinking_process_enabled() -> bool:
    return str(ModelConfig.show_thinking_process).strip().lower() in ("true", "1", "yes")

_TOOL_EXIT_PROTOCOL_RE = re.compile(
    r"\s*\[Command (?P<result>succeeded|failed) with exit code (?P<exit_code>\d+)\]"
    r"(?P<truncated>\s*\[Output was truncated due to size limits\])?\s*$"
)
_TOOL_INPUT_MAX = 65536
TASK_TOOL_NAME = "task"
_OMIT_NON_JSON_TOOL_INPUT = object()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _format_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_done() -> str:
    return "data: [DONE]\n\n"


def _json_safe_tool_input(value: Any) -> Any:
    """只保留模型可见的 JSON 输入，丢弃 ToolRuntime 等框架注入对象。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for key, item in value.items():
            safe_item = _json_safe_tool_input(item)
            if safe_item is not _OMIT_NON_JSON_TOOL_INPUT:
                normalized[str(key)] = safe_item
        return normalized
    if isinstance(value, (list, tuple, set)):
        normalized_items = []
        for item in value:
            safe_item = _json_safe_tool_input(item)
            if safe_item is not _OMIT_NON_JSON_TOOL_INPUT:
                normalized_items.append(safe_item)
        return normalized_items
    return _OMIT_NON_JSON_TOOL_INPUT


def _normalize_tool_input(raw: Any) -> tuple[Dict[str, Any], Optional[str]]:
    """SSE / builder 统一使用 JSON-safe dict；返回前端 input_text。"""
    if raw is None or raw == {}:
        return {}, None
    if isinstance(raw, dict):
        normalized = _json_safe_tool_input(raw)
        dumped = json.dumps(normalized, ensure_ascii=False)
        return normalized, dumped
    safe_raw = _json_safe_tool_input(raw)
    if safe_raw is _OMIT_NON_JSON_TOOL_INPUT:
        return {}, None
    dumped = json.dumps(safe_raw, ensure_ascii=False)
    if len(dumped) > _TOOL_INPUT_MAX:
        dumped = f"{dumped[:_TOOL_INPUT_MAX]}..."
    return {"_tw_tool_input": safe_raw}, dumped


def _tool_output_value(raw_out: Any) -> str:
    if raw_out is None:
        return ""
    return raw_out.content if hasattr(raw_out, "content") else str(raw_out)


def _retrieval_payload(raw: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("results"), list):
        return None
    return parsed


def _extract_tool_result(raw_out: Any, content: str) -> tuple[str, Dict[str, Any]]:
    """Extract explicit result metadata; DeepAgents' anchored suffix is its wire protocol."""
    metadata = extract_process_result(raw_out)
    artifact = getattr(raw_out, "artifact", None)
    if artifact is not None:
        metadata.update(extract_process_result(artifact))
    match = _TOOL_EXIT_PROTOCOL_RE.search(content)
    if match:
        metadata["exit_code"] = int(match.group("exit_code"))
        metadata["truncated"] = bool(match.group("truncated"))
        metadata["timed_out"] = metadata["exit_code"] == 124
        metadata["outcome"] = (
            "timed_out"
            if metadata["timed_out"]
            else "ok"
            if metadata["exit_code"] == 0
            else "command_failed"
        )
        content = content[: match.start()].rstrip()
    return content, metadata


def _resolve_tool_call_id(item: Dict[str, Any], data: Dict[str, Any]) -> str:
    """
    依次尝试：data.tool_call_id → input 内 ToolCall id → run_id（callback 系统强制注入）。
    实践中 run_id 必然存在，最终 fallback 用随机 id 兜底防御。
    """
    tid = data.get("tool_call_id")
    if tid and str(tid).strip():
        return str(tid)
    inp = data.get("input")
    if isinstance(inp, dict):
        tid2 = inp.get("tool_call_id") or inp.get("id")
        if tid2 and str(tid2).strip():
            return str(tid2)
    rid = item.get("run_id")
    if rid and str(rid).strip():
        return str(rid)
    return _new_id("tool")


def _resolve_tool_output_call_id(
    item: Dict[str, Any],
    data: Dict[str, Any],
    ctx: Dict[str, Any],
    tool_part_ids: Dict[str, str],
) -> str:
    """tool-output / tool-error 与 tool-input 对齐。"""
    resolved = _resolve_tool_call_id(item, data)
    if resolved in tool_part_ids:
        return resolved

    current = ctx.get("current_tool_call_id")
    current_s = str(current).strip() if current else ""
    event_name = str(item.get("name") or "")
    current_name = str(ctx.get("current_tool_name") or "")
    # MCP on_tool_error 等场景：回调 id 与模型 tool_call_id 不一致，但工具名一致
    if (
        current_s
        and current_s in tool_part_ids
        and event_name
        and event_name == current_name
    ):
        return current_s
    if current_s:
        return current_s
    return resolved


class LangGraphSseBridge:
    """LangGraph 流事件 → SSE 字符串；可选同步写入 builder。"""

    def __init__(
        self,
        session_id: str,
        *,
        emit_langfuse_session_hint: bool = False,
        assistant_message_id: Optional[str] = None,
    ) -> None:
        self.session_id = session_id or ""
        self._emit_langfuse_session_hint = bool(emit_langfuse_session_hint)
        self.assistant_message_id = str(assistant_message_id) if assistant_message_id else str(uuid.uuid4())
        self._message_started = False
        self._text_open = False
        self._current_text_part_id: Optional[str] = None
        self._reasoning_open = False
        self._current_reasoning_part_id: Optional[str] = None
        self._show_thinking = _show_thinking_process_enabled()
        self._tool_part_ids: Dict[str, str] = {}
        self._current_text_parent_task_call_id: Optional[str] = None
        self._current_reasoning_parent_task_call_id: Optional[str] = None
        self._finish_emitted = False
        self._persist_tick = False
        self.last_finish_usage: Dict[str, Any] = {}
        self.last_finish_reason: str = ""
        self.last_error_message: str = ""
        self._usage_cumulative: Dict[str, Any] = {}
        self.last_context_snapshot: Dict[str, int] = {}
        self._session_context_tick = False
        self.last_hitl_payload: Optional[Dict[str, Any]] = None
        self._usage_collector = RunUsageCollector()

    # ---------- metrics ctx ----------

    @staticmethod
    def _ensure_metrics_ctx(ctx: Dict[str, Any]) -> None:
        if "tool_start_times" not in ctx:
            ctx["tool_start_times"] = {}
        if "usage_cumulative" not in ctx:
            ctx["usage_cumulative"] = {"input_tokens": 0, "output_tokens": 0}
        if "usage_seen_run_ids" not in ctx:
            ctx["usage_seen_run_ids"]: Set[str] = set()

    @staticmethod
    def _ensure_subagent_ctx(ctx: Dict[str, Any]) -> None:
        if "run_id_to_tool_call_id" not in ctx:
            ctx["run_id_to_tool_call_id"] = {}
        if "task_tool_call_stack" not in ctx:
            ctx["task_tool_call_stack"] = []

    @staticmethod
    def _resolve_parent_task_call_id(item: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[str]:
        """子 Agent 内部 tool 归属到当前活跃的 task tool_call_id（支持 parent_ids 与并行 task）。"""
        LangGraphSseBridge._ensure_subagent_ctx(ctx)
        stack: List[str] = ctx["task_tool_call_stack"]
        run_map: Dict[str, str] = ctx["run_id_to_tool_call_id"]
        parent_ids = item.get("parent_ids")
        if isinstance(parent_ids, (list, tuple)):
            for pid in reversed(parent_ids):
                if pid is None:
                    continue
                tid = run_map.get(str(pid))
                if tid:
                    return tid
        return stack[-1] if stack else None

    def _register_tool_run(self, item: Dict[str, Any], tool_call_id: str, ctx: Dict[str, Any]) -> None:
        self._ensure_subagent_ctx(ctx)
        run_id = item.get("run_id")
        if run_id and str(run_id).strip():
            ctx["run_id_to_tool_call_id"][str(run_id)] = tool_call_id

    def _on_task_tool_start(self, tool_call_id: str, ctx: Dict[str, Any]) -> None:
        self._ensure_subagent_ctx(ctx)
        ctx["task_tool_call_stack"].append(tool_call_id)

    def _on_task_tool_end(self, tool_call_id: str, ctx: Dict[str, Any]) -> None:
        self._ensure_subagent_ctx(ctx)
        stack: List[str] = ctx["task_tool_call_stack"]
        if not stack:
            return
        if stack[-1] == tool_call_id:
            stack.pop()
            return
        if tool_call_id in stack:
            stack.remove(tool_call_id)

    def _cumulative_usage(self, ctx: Dict[str, Any]) -> Dict[str, int]:
        self._ensure_metrics_ctx(ctx)
        return dict(ctx["usage_cumulative"])

    def _build_usage_payload(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        if ctx is not None:
            cum = self._cumulative_usage(ctx)
        else:
            cum = dict(self._usage_cumulative)
        if not cum or (cum.get("input_tokens", 0) == 0 and cum.get("output_tokens", 0) == 0):
            return {}
        return cum

    def _emit_usage_update(self, ctx: Dict[str, Any], out: List[str]) -> None:
        usage = self._build_usage_payload(ctx)
        if not usage:
            return
        self._usage_cumulative = dict(usage)
        out.append(_format_sse("usage-update", {
            "type": "usage-update",
            "message_id": self.assistant_message_id,
            "usage": usage,
        }))
        snapshot = ContextMetricsRegistry.peek(self.session_id)
        if snapshot:
            self._emit_context_update(snapshot, out)

    def _accumulate_usage(
        self,
        ctx: Dict[str, Any],
        run_id: Optional[str],
        raw_usage: Any,
        out: List[str],
        parent_task_call_id: Optional[str] = None,
    ) -> None:
        usage = _normalize_usage(raw_usage)
        if not usage:
            return
        self._ensure_metrics_ctx(ctx)
        rid = str(run_id or "").strip()
        if rid:
            seen: Set[str] = ctx["usage_seen_run_ids"]
            if rid in seen:
                return
            seen.add(rid)
        cum: Dict[str, Any] = ctx["usage_cumulative"]
        cum["input_tokens"] = cum.get("input_tokens", 0) + usage.get("input_tokens", 0)
        cum["output_tokens"] = cum.get("output_tokens", 0) + usage.get("output_tokens", 0)
        if "total_tokens" in usage:
            cum["total_tokens"] = cum.get("total_tokens", 0) + usage["total_tokens"]
        elif cum.get("input_tokens") or cum.get("output_tokens"):
            cum["total_tokens"] = cum.get("input_tokens", 0) + cum.get("output_tokens", 0)
        # 累计 detail 子项（cache_read/cache_write/reasoning），缺失不补零
        _accumulate_detail(cum, "input_token_details", usage.get("input_token_details"))
        _accumulate_detail(cum, "output_token_details", usage.get("output_token_details"))
        # 归因：按 caller/model 聚合（task 3.2）
        self._usage_collector.record(
            raw_usage,
            attribution=ModelCallAttribution(
                model_run_id=rid,
                caller=resolve_caller(parent_task_call_id),
                parent_tool_call_id=parent_task_call_id or "",
            ),
        )
        self._usage_cumulative = dict(cum)
        self._emit_usage_update(ctx, out)

    def _tool_duration_ms(self, ctx: Dict[str, Any], tool_call_id: str) -> Optional[int]:
        self._ensure_metrics_ctx(ctx)
        start = ctx["tool_start_times"].pop(tool_call_id, None)
        if start is None:
            return None
        return max(0, int((time.perf_counter() - start) * 1000))

    # ---------- emit helpers ----------

    def _ensure_started(self, out: List[str]) -> None:
        if self._message_started:
            return
        self._message_started = True
        payload: Dict[str, Any] = {
            "type": "message-start",
            "session_id": self.session_id,
            "assistant_message_id": self.assistant_message_id,
        }
        if self._emit_langfuse_session_hint and self.session_id:
            payload["langfuse_session_id"] = self.session_id
        out.append(_format_sse("message-start", payload))

    def _close_text(self, out: List[str], *, record_checkpoint: bool = True) -> None:
        if not self._text_open or not self._current_text_part_id:
            return
        out.append(_format_sse("text-end", {
            "type": "text-end",
            "message_id": self.assistant_message_id,
            "part_id": self._current_text_part_id,
        }))
        self._text_open = False
        self._current_text_part_id = None
        self._current_text_parent_task_call_id = None
        if record_checkpoint:
            self._persist_tick = True

    def _close_reasoning(self, out: List[str], *, record_checkpoint: bool = True) -> None:
        if not self._reasoning_open or not self._current_reasoning_part_id:
            return
        out.append(_format_sse("reasoning-end", {
            "type": "reasoning-end",
            "message_id": self.assistant_message_id,
            "part_id": self._current_reasoning_part_id,
        }))
        self._reasoning_open = False
        self._current_reasoning_part_id = None
        self._current_reasoning_parent_task_call_id = None
        if record_checkpoint:
            self._persist_tick = True

    @staticmethod
    def _sse_parent_field(parent_task_call_id: Optional[str]) -> Dict[str, str]:
        if parent_task_call_id:
            return {"parent_task_call_id": parent_task_call_id}
        return {}

    def consume_persist_tick(self) -> bool:
        """供 QaService 在 part 边界将 builder 快照写库；消费后清零。"""
        if self._persist_tick:
            self._persist_tick = False
            return True
        return False

    def consume_session_context_tick(self) -> bool:
        if self._session_context_tick:
            self._session_context_tick = False
            return True
        return False

    def _emit_context_update(self, snapshot: Dict[str, int], out: List[str]) -> None:
        if not snapshot.get("max_tokens"):
            return
        self.last_context_snapshot = dict(snapshot)
        self._session_context_tick = True
        self._ensure_started(out)
        out.append(_format_sse("context-update", {
            "type": "context-update",
            "message_id": self.assistant_message_id,
            "context": snapshot,
        }))

    def _emit_reasoning_delta(
        self,
        content: str,
        out: List[str],
        parent_task_call_id: Optional[str] = None,
    ) -> None:
        if not content or not self._show_thinking:
            return
        self._ensure_started(out)
        if (
            self._reasoning_open
            and parent_task_call_id != self._current_reasoning_parent_task_call_id
        ):
            self._close_reasoning(out)
        if not self._reasoning_open:
            self._current_reasoning_part_id = _new_id("part-reasoning")
            self._current_reasoning_parent_task_call_id = parent_task_call_id
            out.append(_format_sse("reasoning-start", {
                "type": "reasoning-start",
                "message_id": self.assistant_message_id,
                "part_id": self._current_reasoning_part_id,
                **self._sse_parent_field(parent_task_call_id),
            }))
            self._reasoning_open = True
        out.append(_format_sse("reasoning-delta", {
            "type": "reasoning-delta",
            "message_id": self.assistant_message_id,
            "part_id": self._current_reasoning_part_id,
            "text_delta": content,
            **self._sse_parent_field(parent_task_call_id),
        }))

    def _emit_text_delta(
        self,
        content: str,
        out: List[str],
        parent_task_call_id: Optional[str] = None,
        part_id: Optional[str] = None,
    ) -> None:
        if not content:
            return
        self._close_reasoning(out)
        self._ensure_started(out)
        if self._text_open and parent_task_call_id != self._current_text_parent_task_call_id:
            self._close_text(out)
        if not self._text_open:
            self._current_text_part_id = part_id or _new_id("part-text")
            self._current_text_parent_task_call_id = parent_task_call_id
            out.append(_format_sse("text-start", {
                "type": "text-start",
                "message_id": self.assistant_message_id,
                "part_id": self._current_text_part_id,
                **self._sse_parent_field(parent_task_call_id),
            }))
            self._text_open = True
        out.append(_format_sse("text-delta", {
            "type": "text-delta",
            "message_id": self.assistant_message_id,
            "part_id": self._current_text_part_id,
            "text_delta": content,
            **self._sse_parent_field(parent_task_call_id),
        }))

    def _emit_tool_output(self, out: List[str], part_id: str, tool_call_id: str,
                          output: str, status: str, error: Optional[str],
                          duration_ms: Optional[int] = None,
                          error_category: Optional[str] = None,
                          *, state: ToolState | str,
                          outcome: Optional[str] = None,
                          exit_code: Optional[int] = None,
                          timed_out: Optional[bool] = None,
                          truncated: Optional[bool] = None) -> None:
        payload: Dict[str, Any] = {
            "type": "tool-output-available",
            "message_id": self.assistant_message_id,
            "part_id": part_id,
            "tool_call_id": tool_call_id,
            "output": output,
            "status": status,
            "state": ToolState(str(state)).value,
            "error": error,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if error_category:
            payload["errorCategory"] = error_category
        if outcome is not None:
            payload["outcome"] = outcome
        if exit_code is not None:
            payload["exit_code"] = exit_code
        if timed_out is not None:
            payload["timed_out"] = timed_out
        if truncated is not None:
            payload["truncated"] = truncated
        out.append(_format_sse("tool-output-available", payload))
        self._persist_tick = True

    def _emit_finish(
        self,
        out: List[str],
        payload: Dict[str, Any],
        builder: Optional[AssistantMessageBuilder] = None,
        ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._ensure_started(out)
        self._close_reasoning(out, record_checkpoint=False)
        self._close_text(out, record_checkpoint=False)
        if builder is not None and ctx is not None:
            self._flush_text_buffer(builder, ctx)
        self.last_finish_usage = payload.get("usage") or {}
        self.last_finish_reason = str(payload.get("finish_reason") or "stop")
        # 附带 by_caller/by_model 归因摘要（向后兼容：旧前端忽略 attribution 字段）
        attribution = self._usage_collector.summary()
        if attribution.get("by_caller") or attribution.get("by_model"):
            payload.setdefault("attribution", attribution)
        out.append(_format_sse("finish", payload))
        self._finish_emitted = True

    def _flush_text_buffer(self, builder: Optional[AssistantMessageBuilder], ctx: Dict[str, Any]) -> None:
        buf = ctx.get("text_buffer") or ""
        parent = ctx.get("text_buffer_parent_task_call_id")
        if builder and buf:
            builder.append_text_delta(buf, parent_task_call_id=parent)
        ctx["text_buffer"] = ""
        ctx["text_buffer_parent_task_call_id"] = None

    def _safe_append_tool_output(
        self,
        builder: AssistantMessageBuilder,
        tool_name: str,
        output: str,
        tool_call_id: Optional[str],
        duration_ms: Optional[int] = None,
        *,
        status: str = "success",
        error: Optional[str] = None,
        error_category: Optional[str] = None,
        state: ToolState | str | None = None,
        outcome: Optional[str] = None,
        exit_code: Optional[int] = None,
        timed_out: Optional[bool] = None,
        truncated: Optional[bool] = None,
    ) -> bool:
        try:
            builder.append_tool_output(
                tool_name,
                output,
                tool_call_id,
                duration_ms=duration_ms,
                status=status,
                error=error,
                error_category=error_category,
                state=state,
                outcome=outcome,
                exit_code=exit_code,
                timed_out=timed_out,
                truncated=truncated,
            )
            return True
        except ValueError as e:
            logger.warning(
                "append_tool_output failed: tool={} tool_call_id={} err={}",
                tool_name,
                tool_call_id,
                e,
            )
            return False

    def _task_has_subagent_tool_error(
        self,
        builder: Optional[AssistantMessageBuilder],
        task_tool_call_id: str,
    ) -> bool:
        if builder is None or not task_tool_call_id:
            return False
        return builder.has_failed_child_tool(task_tool_call_id)

    def _resolve_tool_failure(
        self,
        *,
        tool_name: str,
        clean_output: str,
        output_status: Optional[str],
        exc: Optional[BaseException] = None,
        builder: Optional[AssistantMessageBuilder] = None,
        task_tool_call_id: Optional[str] = None,
    ) -> Optional[ToolFailure]:
        if tool_name == TASK_TOOL_NAME:
            if self._task_has_subagent_tool_error(builder, task_tool_call_id or ""):
                return subagent_failure_from_context(clean_output)
            if clean_output and output_status != "error":
                task_failure = classify_task_tool_output(clean_output)
                if task_failure is not None:
                    return task_failure
        if output_status == "error" or exc is not None:
            return classify_tool_failure(exc, raw=clean_output, tool_name=tool_name)
        return None

    # ---------- entry ----------

    def process_item(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                     ctx: Dict[str, Any]) -> List[str]:
        """单条上游事件 → 多条 SSE 行。"""
        out: List[str] = []
        lc_kind = item.get("event")
        if isinstance(lc_kind, str) and lc_kind:
            self._handle_langchain(lc_kind, item, builder, ctx, out)
        else:
            self._handle_tw_or_business(item, builder, ctx, out)
        return out

    def finalize(self, *, finish_reason: Optional[str] = None) -> List[str]:
        """流结束：保证至少发一次 finish，再发 [DONE]。"""
        out: List[str] = []
        had_finish_before = self._finish_emitted
        if not self._finish_emitted:
            usage = self._build_usage_payload()
            self._emit_finish(out, {
                "type": "finish",
                "message_id": self.assistant_message_id,
                "finish_reason": finish_reason or "stop",
                "usage": usage,
            })
        out.append(_format_done())
        logger.info(
            f"SSE bridge finalize session_id={self.session_id} assistant_message_id={self.assistant_message_id} "
            f"synthesized_finish={not had_finish_before}"
        )
        return out

    # ---------- Noesis / 业务事件 ----------

    def _handle_tw_or_business(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                               ctx: Dict[str, Any], out: List[str]) -> None:
        t = item.get("type")

        if t == "__tw_finish__":
            item_usage = item.get("usage") or {}
            usage = item_usage if item_usage else self._build_usage_payload(ctx)
            self._emit_finish(out, {
                "type": "finish",
                "message_id": self.assistant_message_id,
                "finish_reason": item.get("finish_reason") or "stop",
                "usage": usage,
            }, builder=builder, ctx=ctx)
            return

        if t in ("__tw_abort__", "abort"):
            self._ensure_started(out)
            self._close_reasoning(out, record_checkpoint=False)
            self._close_text(out, record_checkpoint=False)
            finish_reason = str(item.get("finish_reason") or "abort")
            if finish_reason == "error":
                out.append(_format_sse("error", {
                    "type": "error",
                    "message_id": self.assistant_message_id,
                    "error": sanitize_stream_error(str(item.get("content") or "生成失败，请稍后重试")),
                }))
            else:
                out.append(_format_sse("abort", {
                    "type": "abort",
                    "message_id": self.assistant_message_id,
                    "reason": finish_reason,
                }))
            self._finish_emitted = True
            logger.info(
                f"SSE bridge 发出 abort session_id={self.session_id} assistant_message_id={self.assistant_message_id} "
                f"upstream_type={t}"
            )
            return

        if t in ("__tw_error__", "error"):
            self._ensure_started(out)
            self._close_reasoning(out, record_checkpoint=False)
            self._close_text(out, record_checkpoint=False)
            msg = sanitize_stream_error(
                str(item.get("error") or item.get("content") or "unknown error")
            )
            self.last_error_message = msg
            logger.warning(
                f"SSE bridge 发出 error session_id={self.session_id} assistant_message_id={self.assistant_message_id} "
                f"detail={str(msg)[:500]}"
            )
            out.append(_format_sse("error", {
                "type": "error",
                "message_id": self.assistant_message_id,
                "error": str(msg),
            }))
            self._finish_emitted = True
            return

        if t == "text-delta":
            delta = str(item.get("text_delta") or "")
            if delta:
                if builder is not None:
                    ctx["text_buffer"] = (ctx.get("text_buffer") or "") + delta
                self._emit_text_delta(delta, out)
            return

        if t == "finish":
            payload = dict(item)
            payload.setdefault("type", "finish")
            payload.setdefault("message_id", self.assistant_message_id)
            if not payload.get("usage"):
                payload["usage"] = self._build_usage_payload(ctx)
            self._emit_finish(out, payload, builder=builder, ctx=ctx)
            return

        if t == "hitl-required":
            self._ensure_started(out)
            self._close_reasoning(out)
            self._close_text(out)
            if builder is not None:
                self._flush_text_buffer(builder, ctx)
            payload = dict(item)
            payload["type"] = "hitl-required"
            payload["message_id"] = self.assistant_message_id
            payload.setdefault("session_id", self.session_id)
            self.last_hitl_payload = payload
            parent_task_call_id = self._resolve_parent_task_call_id(item, ctx)
            for action in payload.get("action_requests") or []:
                name = str(action.get("name") or "")
                tool_call_id = action.get("tool_call_id") or ""
                args = action.get("args") if isinstance(action.get("args"), dict) else {}
                already = bool(
                    tool_call_id
                    and builder is not None
                    and builder.get_tool(tool_call_id) is not None
                )
                if already and builder is not None:
                    builder.update_tool_hitl(
                        tool_call_id,
                        {
                            "kind": payload.get("kind"),
                            "status": "pending",
                            "interrupt_id": payload.get("interrupt_id"),
                        },
                        status="running",
                        state=ToolState.APPROVAL_PENDING,
                    )
                else:
                    part_id = _new_id("part-tool")
                    if tool_call_id:
                        self._tool_part_ids[tool_call_id] = part_id
                    out.append(
                        _format_sse(
                            "tool-input-start",
                            {
                                "type": "tool-input-start",
                                "message_id": self.assistant_message_id,
                                "part_id": part_id,
                                "tool_call_id": tool_call_id,
                                "name": name,
                                "state": ToolState.APPROVAL_PENDING.value,
                                **self._sse_parent_field(parent_task_call_id),
                            },
                        )
                    )
                    out.append(
                        _format_sse(
                            "tool-input-available",
                            {
                                "type": "tool-input-available",
                                "message_id": self.assistant_message_id,
                                "part_id": part_id,
                                "tool_call_id": tool_call_id,
                                "name": name,
                                "input": args,
                                "state": ToolState.APPROVAL_PENDING.value,
                                **self._sse_parent_field(parent_task_call_id),
                            },
                        )
                    )
                    if builder is not None:
                        builder.append_tool(
                            name,
                            args,
                            tool_call_id=tool_call_id or None,
                            parent_task_call_id=parent_task_call_id,
                            status="running",
                            state=ToolState.APPROVAL_PENDING,
                            hitl={
                                "kind": payload.get("kind"),
                                "status": "pending",
                                "interrupt_id": payload.get("interrupt_id"),
                            },
                        )
            self._persist_tick = True
            out.append(_format_sse("hitl-required", payload))
            return

        if t in ("phase-start", "phase-delta", "phase-end"):
            self._ensure_started(out)
            payload = dict(item)
            payload.setdefault("type", str(t))
            payload.setdefault("message_id", self.assistant_message_id)
            if t == "phase-end":
                payload.setdefault("ok", True)
                self._persist_tick = True
            out.append(_format_sse(str(t), payload))
            return

        if t and t not in ("ai", "tool"):
            self._ensure_started(out)
            payload = dict(item)
            payload.setdefault("message_id", self.assistant_message_id)
            out.append(_format_sse(str(t), payload))

    # ---------- LangChain astream_events ----------

    def _handle_langchain(self, lc_kind: str, item: Dict[str, Any],
                          builder: Optional[AssistantMessageBuilder],
                          ctx: Dict[str, Any], out: List[str]) -> None:
        if lc_kind == "on_custom_event" and item.get("name") == "noesis_model_retry":
            data = item.get("data") or {}
            if isinstance(data, dict):
                payload = {"type": "run-status", **data}
                out.append(_format_sse("run-status", payload))
            return

        if lc_kind == "on_chat_model_start":
            self._close_reasoning(out, record_checkpoint=False)
            self._close_text(out, record_checkpoint=False)
            return

        if lc_kind == "on_chat_model_stream":
            parent_task_call_id = self._resolve_parent_task_call_id(item, ctx)
            chunk = (item.get("data") or {}).get("chunk")
            if self._show_thinking and chunk is not None:
                reasoning_delta = extract_reasoning_delta(chunk)
                if reasoning_delta:
                    if builder is not None:
                        builder.append_reasoning_delta(
                            reasoning_delta,
                            parent_task_call_id=parent_task_call_id,
                        )
                    ctx["reasoning_buffer"] = (ctx.get("reasoning_buffer") or "") + reasoning_delta
                    self._emit_reasoning_delta(reasoning_delta, out, parent_task_call_id)
            content = extract_text_content(chunk) if chunk is not None else ""
            if content:
                if builder is not None:
                    ctx["text_buffer"] = (ctx.get("text_buffer") or "") + content
                    ctx["text_buffer_parent_task_call_id"] = parent_task_call_id
                self._emit_text_delta(content, out, parent_task_call_id)
            # usage 不在 stream chunk 累计：部分 chunk 的 usage 不完整，且会因 run_id 去重
            # 让 on_chat_model_end 的权威 usage 被丢弃。usage 统一在 on_chat_model_end 累计。
            return

        if lc_kind == "on_chat_model_end":
            data = item.get("data") or {}
            output = data.get("output")
            parent_task_call_id = self._resolve_parent_task_call_id(item, ctx)
            if output is not None:
                if self._show_thinking:
                    final_reasoning = extract_reasoning_delta(output)
                    reasoning_delta = unsent_text_suffix(
                        final_reasoning or "",
                        str(ctx.get("reasoning_buffer") or ""),
                    )
                    if reasoning_delta:
                        if builder is not None:
                            builder.append_reasoning_delta(
                                reasoning_delta,
                                parent_task_call_id=parent_task_call_id,
                            )
                        ctx["reasoning_buffer"] = (ctx.get("reasoning_buffer") or "") + reasoning_delta
                        self._emit_reasoning_delta(reasoning_delta, out, parent_task_call_id)
                final_text = extract_text_content(output)
                text_delta = unsent_text_suffix(final_text, str(ctx.get("text_buffer") or ""))
                if text_delta:
                    if builder is not None:
                        ctx["text_buffer"] = (ctx.get("text_buffer") or "") + text_delta
                        ctx["text_buffer_parent_task_call_id"] = parent_task_call_id
                    self._emit_text_delta(text_delta, out, parent_task_call_id)
            usage_meta = getattr(output, "usage_metadata", None) if output is not None else None
            if not usage_meta and isinstance(output, dict):
                usage_meta = output.get("usage_metadata")
            if usage_meta:
                self._accumulate_usage(ctx, item.get("run_id"), usage_meta, out, parent_task_call_id)
            return

        if lc_kind == "on_tool_start":
            self._on_tool_start(item, builder, ctx, out)
            return

        if lc_kind == "on_tool_end":
            self._on_tool_end(item, builder, ctx, out)
            return

        if lc_kind == "on_tool_error":
            self._on_tool_error(item, builder, ctx, out)
            return

    def _on_tool_start(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                       ctx: Dict[str, Any], out: List[str]) -> None:
        self._ensure_started(out)
        self._close_reasoning(out)
        self._close_text(out)
        if builder is not None:
            self._flush_text_buffer(builder, ctx)

        data = item.get("data") or {}
        tool_name = item.get("name") or ""
        input_obj, input_text = _normalize_tool_input(data.get("input", {}))
        tool_call_id = _resolve_tool_call_id(item, data)
        if builder is not None and tool_call_id not in self._tool_part_ids:
            resumed_tool_call_id = builder.resolve_hitl_tool_call_id(tool_name, input_obj)
            if resumed_tool_call_id:
                tool_call_id = resumed_tool_call_id
        self._register_tool_run(item, tool_call_id, ctx)

        parent_task_call_id: Optional[str] = None
        if tool_name == TASK_TOOL_NAME:
            self._on_task_tool_start(tool_call_id, ctx)
        else:
            parent_task_call_id = self._resolve_parent_task_call_id(item, ctx)

        self._ensure_metrics_ctx(ctx)
        ctx["tool_start_times"][tool_call_id] = time.perf_counter()

        ctx["current_tool_name"] = tool_name
        ctx["current_tool_call_id"] = tool_call_id

        if builder is not None:
            builder.append_tool(
                tool_name,
                input_obj,
                tool_call_id,
                parent_task_call_id=parent_task_call_id,
                state=ToolState.RUNNING,
            )

        part_id = _new_id("part-tool")
        if tool_call_id:
            self._tool_part_ids[tool_call_id] = part_id

        start_payload: Dict[str, Any] = {
            "type": "tool-input-start",
            "message_id": self.assistant_message_id,
            "part_id": part_id,
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "state": ToolState.RUNNING.value,
        }
        if parent_task_call_id:
            start_payload["parent_task_call_id"] = parent_task_call_id
        out.append(_format_sse("tool-input-start", start_payload))
        avail: Dict[str, Any] = {
            "type": "tool-input-available",
            "message_id": self.assistant_message_id,
            "part_id": part_id,
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "input": input_obj,
            "state": ToolState.RUNNING.value,
        }
        if parent_task_call_id:
            avail["parent_task_call_id"] = parent_task_call_id
        if input_text is not None:
            avail["input_text"] = input_text
        out.append(_format_sse("tool-input-available", avail))

    def _on_tool_end(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                     ctx: Dict[str, Any], out: List[str]) -> None:
        self._ensure_started(out)
        self._close_reasoning(out)
        self._close_text(out)

        data = item.get("data") or {}
        raw_output = data.get("output")
        raw_content = _tool_output_value(raw_output) if raw_output else ""
        clean_output, process_result = _extract_tool_result(raw_output, raw_content)
        tool_call_id = _resolve_tool_output_call_id(item, data, ctx, self._tool_part_ids)
        tool_name = item.get("name") or ctx.get("current_tool_name") or ""
        ctx["current_tool_name"] = tool_name
        ctx["current_tool_call_id"] = tool_call_id

        duration_ms = self._tool_duration_ms(ctx, tool_call_id)
        output_status = getattr(raw_output, "status", None) if raw_output is not None else None
        failure = self._resolve_tool_failure(
            tool_name=tool_name,
            clean_output=clean_output,
            output_status=output_status,
            builder=builder,
            task_tool_call_id=tool_call_id if tool_name == TASK_TOOL_NAME else None,
        )
        outcome = process_result.get("outcome")
        exit_code = process_result.get("exit_code")
        timed_out = process_result.get("timed_out")
        truncated = process_result.get("truncated")
        is_error = failure is not None or output_status == "error"
        err_fields = failure_to_sse_error_fields(failure) if failure else {}
        err_s = err_fields.get("error") if is_error else None
        err_cat = err_fields.get("errorCategory") if is_error else None
        if outcome == "command_failed":
            err_cat = "command_failed"
        elif outcome == "timed_out":
            err_cat = "execution_timeout"
        if is_error and not err_s:
            err_s = sanitize_tool_error(clean_output)
        sse_status = "error" if is_error else "success"
        state = derive_tool_state(
            status=sse_status,
            outcome=outcome,
            error_category=err_cat,
            timed_out=timed_out,
        )
        display_output = "" if is_error else clean_output
        builder_output = clean_output if not is_error else (failure.message_for_llm if failure else clean_output)

        if builder is not None:
            self._safe_append_tool_output(
                builder,
                tool_name,
                builder_output,
                tool_call_id,
                duration_ms=duration_ms,
                status="error" if is_error else "success",
                error=err_s if is_error else None,
                error_category=err_cat if is_error else None,
                state=state,
                outcome=outcome,
                exit_code=exit_code,
                timed_out=timed_out,
                truncated=truncated,
            )

        retrieval_part = None
        if (
            builder is not None
            and not is_error
            and tool_name in {"search_knowledge_base", "web_search", "web_fetch"}
        ):
            retrieval_payload = _retrieval_payload(clean_output)
            if retrieval_payload is not None:
                tool_part = builder.get_tool(tool_call_id)
                tool_input = tool_part.arguments if tool_part is not None else {}
                retrieval_part = builder.register_retrieval_results(
                    tool_call_id=tool_call_id,
                    query=str((tool_input or {}).get("query") or (tool_input or {}).get("url") or ""),
                    results=retrieval_payload["results"],
                    truncated=bool(retrieval_payload.get("truncated")),
                )
                if tool_part is not None:
                    tool_part.output = f"检索到 {len(retrieval_part.results)} 条来源"

        if tool_name == TASK_TOOL_NAME:
            self._on_task_tool_end(tool_call_id, ctx)

        part_id = self._tool_part_ids.get(tool_call_id) or _new_id("part-tool")
        self._emit_tool_output(
            out, part_id, tool_call_id, display_output, sse_status, err_s, duration_ms,
            error_category=err_cat,
            state=state,
            outcome=outcome,
            exit_code=exit_code,
            timed_out=timed_out,
            truncated=truncated,
        )
        if retrieval_part is not None:
            payload = retrieval_part.to_dict()
            payload["type"] = "retrieval-results-available"
            payload["message_id"] = self.assistant_message_id
            out.append(_format_sse("retrieval-results-available", payload))
            self._persist_tick = True

    def _on_tool_error(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                       ctx: Dict[str, Any], out: List[str]) -> None:
        self._ensure_started(out)
        self._close_reasoning(out)
        self._close_text(out)

        data = item.get("data") or {}
        raw_err = data.get("error")
        err_text = str(raw_err) if raw_err is not None else ""
        tool_call_id = _resolve_tool_output_call_id(item, data, ctx, self._tool_part_ids)
        tool_name = item.get("name") or ctx.get("current_tool_name") or ""
        ctx["current_tool_name"] = tool_name
        ctx["current_tool_call_id"] = tool_call_id

        exc = raw_err if isinstance(raw_err, BaseException) else None
        failure = self._resolve_tool_failure(
            tool_name=tool_name,
            clean_output=err_text,
            output_status="error",
            exc=exc,
            builder=builder,
            task_tool_call_id=tool_call_id if tool_name == TASK_TOOL_NAME else None,
        )
        err_fields = failure_to_sse_error_fields(failure) if failure else {}
        err_s = err_fields.get("error") or sanitize_tool_error(f"Tool error: {err_text}")
        err_cat = err_fields.get("errorCategory")
        duration_ms = self._tool_duration_ms(ctx, tool_call_id)

        if builder is not None:
            ok = self._safe_append_tool_output(
                builder,
                tool_name,
                failure.message_for_llm if failure else err_s,
                tool_call_id,
                duration_ms=duration_ms,
                status="error",
                error=err_s,
                error_category=err_cat,
                state=derive_tool_state(status="error", error_category=err_cat),
            )
            if not ok:
                builder.append_text(err_s)

        if tool_name == TASK_TOOL_NAME:
            self._on_task_tool_end(tool_call_id, ctx)

        part_id = self._tool_part_ids.get(tool_call_id) or _new_id("part-tool")
        self._emit_tool_output(
            out, part_id, tool_call_id, "", "error", err_s, duration_ms,
            error_category=err_cat,
            state=derive_tool_state(status="error", error_category=err_cat),
        )


def bridge_raw_to_sse_lines(
    raw: Any,
    bridge: LangGraphSseBridge,
    builder: Optional[AssistantMessageBuilder],
    ctx: Dict[str, Any],
    *,
    keepalive_comment: str,
) -> Optional[List[str]]:
    """将 MemoryStreamBridge 单条原始事件转为 SSE 行；``None`` 表示结束哨兵应跳过。"""
    if raw is HEARTBEAT_SENTINEL:
        return [keepalive_comment]
    if raw is END_SENTINEL:
        return None
    if isinstance(raw, StreamBridgeError):
        raise raw.exc
    return bridge.process_item(raw, builder, ctx)
