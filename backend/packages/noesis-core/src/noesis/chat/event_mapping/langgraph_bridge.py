"""
LangGraph / LangChain astream_events → Noesis 标准 SSE，并同步累积 AssistantMessageBuilder。

仅保留 astream 原始事件 + 少量 __tw_* 控制哨兵（见 base_agent）。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import replace
from typing import Any, Dict, List, Optional

from noesis.runtime.observability import ContextMetricsRegistry
from noesis.llm.model_limits import resolve_context_max_tokens
from noesis.config.env import ModelConfig
from noesis.chat.event_mapping.reasoning import (
    extract_reasoning_delta,
    extract_text_content,
    unsent_text_suffix,
)
from noesis.chat.event_mapping.usage_normalize import (
    compute_used_percentage,
    normalize_usage as _normalize_usage,
)
from noesis.runtime.logging import logger
from noesis.chat.delivery.events import RunEvent, StreamDone, WireFrame
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.tool_state import (
    ToolState,
    derive_tool_state,
    extract_process_result,
)
from noesis.chat.event_mapping.bridge import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridgeError
from noesis.chat.event_mapping.failure_notice import sanitize_stream_error, sanitize_tool_error
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
_TOOL_OUTPUT_DISPLAY_MAX = 24000
TASK_TOOL_NAME = "task"
_OMIT_NON_JSON_TOOL_INPUT = object()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _format_sse(event: str, data: Dict[str, Any]) -> RunEvent:
    """构造 typed wire frame；字符串编码只允许在 Delivery 边界发生。"""
    return WireFrame(event=event, data=dict(data))


def _format_done() -> RunEvent:
    return StreamDone()


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


def _bound_tool_output_for_display(value: str) -> tuple[str, bool]:
    """限制发往 UI 的单次工具输出，不影响模型侧原始工具结果。"""
    if len(value) <= _TOOL_OUTPUT_DISPLAY_MAX:
        return value, False
    return (
        f"{value[:_TOOL_OUTPUT_DISPLAY_MAX]}\n\n"
        f"…（工具输出过长，已截断展示）",
        True,
    )


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
        model_id: Optional[str] = None,
    ) -> None:
        self.session_id = session_id or ""
        self._model_id = model_id
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
        self.last_finish_reason: str = ""
        self.last_error_message: str = ""
        self._current_attempt_id = 1
        self._model_attempt_ids: Dict[str, int] = {}
        self.last_context_snapshot: Dict[str, int] = {}
        self._session_context_tick = False
        self.last_hitl_payload: Optional[Dict[str, Any]] = None

    # ---------- metrics ctx ----------

    @staticmethod
    def _ensure_metrics_ctx(ctx: Dict[str, Any]) -> None:
        if "tool_start_times" not in ctx:
            ctx["tool_start_times"] = {}

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

    def _mint_step_id(self, ctx: Dict[str, Any], parent_task_call_id: Optional[str]) -> Optional[str]:
        """为当前 model step 的并行工具调用 mint 分组 step_id。

        ``on_chat_model_start`` 把 scope 加入 ``pending_model_step_scopes``；
        本方法在首个 ``on_tool_start`` 时 mint 新 step_id（按 scope 递增），后续同
        step 的工具复用同一 step_id。model step 若不产 tool 则永不 mint（不分组）。
        scope = parent_task_call_id or "root"，顶层与每个子 Agent 独立计数。
        """
        scope = parent_task_call_id or "root"
        pending = ctx.get("pending_model_step_scopes")
        if pending and scope in pending:
            pending.discard(scope)
            counters = ctx.setdefault("step_counters", {})
            n = counters.get(scope, 0) + 1
            counters[scope] = n
            ctx.setdefault("current_step_ids", {})[scope] = f"{scope}:{n}"
        return ctx.get("current_step_ids", {}).get(scope)

    def _resolve_tool_step_id(
        self, builder: Optional[AssistantMessageBuilder], tool_call_id: str, ctx: Dict[str, Any],
    ) -> Optional[str]:
        """tool-output-available 回传与 start 一致的 step_id。

        优先读 builder 里 ToolPart 已记录的 step_id（跨乱序 on_tool_end 仍精确匹配）。
        ``on_tool_end`` 总在 ``on_tool_start`` 之后触发，ToolPart 已带 step_id，
        故此 fallback 极少命中；命中则返回 None（不带 step_id）而非猜测 root scope，
        避免把子 Agent 工具错挂到顶层 step_id。
        """
        if builder is not None:
            tool_part = builder.get_tool(tool_call_id)
            if tool_part is not None and tool_part.step_id:
                return tool_part.step_id
        return None



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

        # 单轮真实 input_tokens → 更新上下文指示器（非累计，每次覆盖）。
        # 仅主对话（无 parent_task_call_id）写入，子 Agent 的 input_tokens 不覆盖圆环。
        # provider 不返回 usage 时 current_input 为空，registry 保留上一轮真实值。
        if parent_task_call_id:
            return
        current_input = usage.get("input_tokens")
        if current_input and current_input > 0:
            limit = resolve_context_max_tokens(self._model_id)
            snapshot = {
                "current_tokens": int(current_input),
                "max_tokens": limit,
                "used_percentage": compute_used_percentage(int(current_input), limit),
            }
            ContextMetricsRegistry.put(self.session_id, snapshot, run_id=rid)
            self._emit_context_update(snapshot, out)

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

    def _close_text(self, out: List[str]) -> None:
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

    def _close_reasoning(self, out: List[str]) -> None:
        if not self._reasoning_open or not self._current_reasoning_part_id:
            return
        out.append(_format_sse("reasoning-end", {
            "type": "reasoning-end",
            "message_id": self.assistant_message_id,
            "part_id": self._current_reasoning_part_id,
            **self._sse_parent_field(self._current_reasoning_parent_task_call_id),
        }))
        self._reasoning_open = False
        self._current_reasoning_part_id = None
        self._current_reasoning_parent_task_call_id = None

    @staticmethod
    def _sse_parent_field(parent_task_call_id: Optional[str]) -> Dict[str, str]:
        if parent_task_call_id:
            return {"parent_task_call_id": parent_task_call_id}
        return {}

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
                          truncated: Optional[bool] = None,
                          step_id: Optional[str] = None) -> None:
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
        if step_id:
            payload["step_id"] = step_id
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

    def _emit_finish(
        self,
        out: List[str],
        payload: Dict[str, Any],
        builder: Optional[AssistantMessageBuilder] = None,
        ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._ensure_started(out)
        self._close_reasoning(out)
        self._close_text(out)
        if builder is not None and ctx is not None:
            self._flush_text_buffer(builder, ctx)
        self.last_finish_reason = str(payload.get("finish_reason") or "stop")
        out.append(_format_sse("finish", payload))
        self._finish_emitted = True

    def _flush_text_buffer(self, builder: Optional[AssistantMessageBuilder], ctx: Dict[str, Any]) -> None:
        buf = ctx.get("text_buffer") or ""
        parent = ctx.get("text_buffer_parent_task_call_id")
        pending = ctx.pop("pending_error_fallback", None)
        if builder:
            # 降级 AIMessage 的 text 可能为空（理论上不会，但防御），
            # 但 pending_error_fallback 存在时仍需创建 text part 来挂标记
            if buf:
                builder.append_text_delta(buf, parent_task_call_id=parent)
            elif pending:
                builder.append_text("", parent_task_call_id=parent)
        ctx["text_buffer"] = ""
        ctx["text_buffer_parent_task_call_id"] = None
        # text 已写入 builder，现在可以标记 error_fallback（on_chat_model_end 暂存的）
        if pending and builder:
            builder.mark_last_text_error_fallback(
                pending["parent_task_call_id"],
                pending["fallback"],
            )

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
            if clean_output and output_status != "error":
                task_failure = classify_task_tool_output(clean_output)
                if task_failure is not None:
                    return task_failure
                # The task wrapper is authoritative when it explicitly reports
                # success. A child tool may fail and still be recovered from by
                # the subagent; keep that child failure visible in its own part
                # without upgrading the parent task to a failure.
                if clean_output.lstrip().startswith("Task Succeeded. Result:"):
                    return None
            # Only use child failures as a fallback when the task has not
            # provided an explicit success/failure result of its own.
            if self._task_has_subagent_tool_error(builder, task_tool_call_id or ""):
                return subagent_failure_from_context(clean_output)
        if output_status == "error" or exc is not None:
            return classify_tool_failure(exc, raw=clean_output, tool_name=tool_name)
        return None

    # ---------- entry ----------

    def map_item(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                 ctx: Dict[str, Any]) -> List[RunEvent]:
        """单条上游 raw event → typed RunEvent。"""
        out: List[RunEvent] = []
        lc_kind = item.get("event")
        raw_run_id = str(item.get("run_id") or "")
        if lc_kind == "on_chat_model_start" and raw_run_id:
            self._model_attempt_ids[raw_run_id] = self._current_attempt_id
        event_attempt_id = self._model_attempt_ids.get(
            raw_run_id, self._current_attempt_id
        )
        if isinstance(lc_kind, str) and lc_kind:
            self._handle_langchain(lc_kind, item, builder, ctx, out)
        else:
            self._handle_tw_or_business(item, builder, ctx, out)
        stamped = [
            replace(event, attempt_id=event_attempt_id)
            if isinstance(event, WireFrame)
            else event
            for event in out
        ]
        if lc_kind == "on_custom_event" and item.get("name") == "noesis_model_retry":
            data = item.get("data") or {}
            if isinstance(data, dict):
                next_attempt = int(data.get("attempt_id") or self._current_attempt_id)
                self._current_attempt_id = max(self._current_attempt_id, next_attempt)
        return stamped

    def finalize_events(self, *, finish_reason: Optional[str] = None) -> List[RunEvent]:
        """流结束：保证至少产生一次 terminal intent，再产生传输收尾标记。"""
        out: List[RunEvent] = []
        had_finish_before = self._finish_emitted
        if not self._finish_emitted:
            self._emit_finish(out, {
                "type": "finish",
                "message_id": self.assistant_message_id,
                "finish_reason": finish_reason or "stop",
            })
        out.append(_format_done())
        logger.info(
            f"SSE bridge finalize session_id={self.session_id} assistant_message_id={self.assistant_message_id} "
            f"synthesized_finish={not had_finish_before}"
        )
        return out

    def process_item(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                     ctx: Dict[str, Any]) -> List[str]:
        """仅供独立 TEST_CASE_QA 旧 SSE 边界使用。"""
        from noesis.chat.delivery.sse import encode_run_event

        return [line for event in self.map_item(item, builder, ctx) for line in encode_run_event(event)]

    def finalize(self, *, finish_reason: Optional[str] = None) -> List[str]:
        """仅供独立 TEST_CASE_QA 旧 SSE 边界使用。"""
        from noesis.chat.delivery.sse import encode_run_event

        return [
            line
            for event in self.finalize_events(finish_reason=finish_reason)
            for line in encode_run_event(event)
        ]

    # ---------- Noesis / 业务事件 ----------

    def _handle_tw_or_business(self, item: Dict[str, Any], builder: Optional[AssistantMessageBuilder],
                               ctx: Dict[str, Any], out: List[str]) -> None:
        t = item.get("type")

        if t == "__tw_finish__":
            self._emit_finish(out, {
                "type": "finish",
                "message_id": self.assistant_message_id,
                "finish_reason": item.get("finish_reason") or "stop",
            }, builder=builder, ctx=ctx)
            return

        if t in ("__tw_abort__", "abort"):
            self._ensure_started(out)
            self._close_reasoning(out)
            self._close_text(out)
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
            self._close_reasoning(out)
            self._close_text(out)
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
            payload.pop("usage", None)
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
            out.append(_format_sse("hitl-required", payload))
            return

        if t in ("phase-start", "phase-delta", "phase-end"):
            self._ensure_started(out)
            payload = dict(item)
            payload.setdefault("type", str(t))
            payload.setdefault("message_id", self.assistant_message_id)
            if t == "phase-end":
                payload.setdefault("ok", True)
            out.append(_format_sse(str(t), payload))
            return

        if t in {"scenario-start", "testpoints-confirm-required", "scene-cases"}:
            # 仅供未迁移的 TEST_CASE_QA / CaseCoordinator 独立边界使用。
            self._ensure_started(out)
            payload = dict(item)
            payload.setdefault("message_id", self.assistant_message_id)
            out.append(_format_sse(str(t), payload))
            return

        if t and t not in ("ai", "tool"):
            logger.warning(
                "丢弃未知 runtime event session_id={} assistant_message_id={} type={}",
                self.session_id,
                self.assistant_message_id,
                t,
            )

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
            self._close_reasoning(out)
            self._close_text(out)
            # 标记该 scope 下一次 on_tool_start 要 mint 新 step_id（并行工具分组）。
            scope = self._resolve_parent_task_call_id(item, ctx) or "root"
            ctx.setdefault("pending_model_step_scopes", set()).add(scope)
            return

        if lc_kind == "on_chat_model_stream":
            parent_task_call_id = self._resolve_parent_task_call_id(item, ctx)
            chunk = (item.get("data") or {}).get("chunk")
            reasoning_delta = extract_reasoning_delta(chunk) if chunk is not None else ""
            if self._show_thinking and reasoning_delta:
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
                # LLM 重试耗尽后的降级 AIMessage：暂存 fallback 标记，
                # 等 _flush_text_buffer 把 text 写入 builder 后再标记 text part
                additional_kwargs = getattr(output, "additional_kwargs", None) or {}
                if isinstance(additional_kwargs, dict) and additional_kwargs.get("noesis_error_fallback"):
                    ctx["pending_error_fallback"] = {
                        "parent_task_call_id": parent_task_call_id,
                        "fallback": {
                            "error_type": additional_kwargs.get("error_type"),
                            "error_reason": additional_kwargs.get("error_reason"),
                            "error_detail": additional_kwargs.get("error_detail"),
                        },
                    }
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

        logger.debug(
            "忽略 LangChain chain event session_id={} event={} name={}",
            self.session_id,
            lc_kind,
            item.get("name"),
        )

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

        step_id = self._mint_step_id(ctx, parent_task_call_id)

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
                step_id=step_id,
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
        if step_id:
            start_payload["step_id"] = step_id
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
        if step_id:
            avail["step_id"] = step_id
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
        display_output, display_truncated = _bound_tool_output_for_display(display_output)
        truncated = bool(truncated) or display_truncated
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
        step_id = self._resolve_tool_step_id(builder, tool_call_id, ctx)
        self._emit_tool_output(
            out, part_id, tool_call_id, display_output, sse_status, err_s, duration_ms,
            error_category=err_cat,
            state=state,
            outcome=outcome,
            exit_code=exit_code,
            timed_out=timed_out,
            truncated=truncated,
            step_id=step_id,
        )
        if retrieval_part is not None:
            payload = retrieval_part.to_dict()
            payload["type"] = "retrieval-results-available"
            payload["message_id"] = self.assistant_message_id
            out.append(_format_sse("retrieval-results-available", payload))

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
        step_id = self._resolve_tool_step_id(builder, tool_call_id, ctx)
        self._emit_tool_output(
            out, part_id, tool_call_id, "", "error", err_s, duration_ms,
            error_category=err_cat,
            state=derive_tool_state(status="error", error_category=err_cat),
            step_id=step_id,
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
