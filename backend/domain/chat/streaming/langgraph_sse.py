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

from agent.middlewares.context_metrics_middleware import ContextMetricsRegistry
from config.env import ModelConfig
from domain.chat.streaming.reasoning import (
    extract_reasoning_delta,
    extract_text_content,
    unsent_text_suffix,
)
from common.logging import logger
from domain.chat.message_builder import AssistantMessageBuilder, ToolPart
from domain.chat.streaming.bridge import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridgeError
from domain.chat.streaming.failure_notice import sanitize_stream_error, sanitize_tool_error
from domain.chat.streaming.tool_failure import (
    ToolFailure,
    classify_task_tool_output,
    classify_tool_failure,
    failure_to_sse_error_fields,
    subagent_failure_from_context,
)


def _show_thinking_process_enabled() -> bool:
    return str(ModelConfig.show_thinking_process).strip().lower() in ("true", "1", "yes")

_TOOL_EXIT_SUFFIX = re.compile(r"\s*\[Command succeeded with exit code \d+\]\s*$")
_TOOL_INPUT_MAX = 65536
TASK_TOOL_NAME = "task"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _format_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_done() -> str:
    return "data: [DONE]\n\n"


def _normalize_tool_input(raw: Any) -> tuple[Dict[str, Any], Optional[str]]:
    """SSE / builder 统一使用 dict 形态的 input；返回 (dict, 原始 JSON 字符串供前端 input_text)。"""
    if raw is None or raw == {}:
        return {}, None
    if isinstance(raw, dict):
        return raw, json.dumps(raw, ensure_ascii=False)
    try:
        dumped = json.dumps(raw, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        dumped = str(raw)
    if len(dumped) > _TOOL_INPUT_MAX:
        dumped = f"{dumped[:_TOOL_INPUT_MAX]}..."
    return {"_tw_tool_input": raw if not isinstance(raw, (set,)) else list(raw)}, dumped


def _tool_output_value(raw_out: Any) -> str:
    if raw_out is None:
        return ""
    return raw_out.content if hasattr(raw_out, "content") else str(raw_out)


def _normalize_usage(raw: Any) -> Dict[str, int]:
    """将 provider usage_metadata 统一为 input_tokens / output_tokens / total_tokens。"""
    if not raw or not isinstance(raw, dict):
        return {}
    key_map = {
        "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
        "output_tokens": ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    out: Dict[str, int] = {}
    for canonical, aliases in key_map.items():
        for k in aliases:
            v = raw.get(k)
            if v is not None:
                try:
                    out[canonical] = int(v)
                except (TypeError, ValueError):
                    pass
                break
    if "total_tokens" not in out and "input_tokens" in out and "output_tokens" in out:
        out["total_tokens"] = out["input_tokens"] + out["output_tokens"]
    return out


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
    ) -> None:
        self.session_id = session_id or ""
        self._emit_langfuse_session_hint = bool(emit_langfuse_session_hint)
        self.assistant_message_id = str(uuid.uuid4())
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
        self._usage_cumulative: Dict[str, int] = {}
        self.last_context_snapshot: Dict[str, int] = {}
        self._session_context_tick = False

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
        if not stack:
            return None
        run_map: Dict[str, str] = ctx["run_id_to_tool_call_id"]
        stack_set = set(stack)
        parent_ids = item.get("parent_ids")
        if isinstance(parent_ids, (list, tuple)):
            for pid in reversed(parent_ids):
                if pid is None:
                    continue
                tid = run_map.get(str(pid))
                if tid and tid in stack_set:
                    return tid
        return stack[-1]

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
        cum: Dict[str, int] = ctx["usage_cumulative"]
        cum["input_tokens"] = cum.get("input_tokens", 0) + usage.get("input_tokens", 0)
        cum["output_tokens"] = cum.get("output_tokens", 0) + usage.get("output_tokens", 0)
        if "total_tokens" in usage:
            cum["total_tokens"] = cum.get("total_tokens", 0) + usage["total_tokens"]
        elif cum.get("input_tokens") or cum.get("output_tokens"):
            cum["total_tokens"] = cum.get("input_tokens", 0) + cum.get("output_tokens", 0)
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
    ) -> None:
        if not content:
            return
        self._close_reasoning(out)
        self._ensure_started(out)
        if self._text_open and parent_task_call_id != self._current_text_parent_task_call_id:
            self._close_text(out)
        if not self._text_open:
            self._current_text_part_id = _new_id("part-text")
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
                          error_category: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {
            "type": "tool-output-available",
            "message_id": self.assistant_message_id,
            "part_id": part_id,
            "tool_call_id": tool_call_id,
            "output": output,
            "status": status,
            "error": error,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if error_category:
            payload["errorCategory"] = error_category
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
        for part in builder._content.parts:  # noqa: SLF001 — bridge 需读累积 parts
            if (
                isinstance(part, ToolPart)
                and part.parent_task_call_id == task_tool_call_id
                and part.status == "error"
            ):
                return True
        return False

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
            out.append(_format_sse("abort", {"type": "abort", "message_id": self.assistant_message_id}))
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
            out.append(_format_sse(str(t), dict(item)))

    # ---------- LangChain astream_events ----------

    def _handle_langchain(self, lc_kind: str, item: Dict[str, Any],
                          builder: Optional[AssistantMessageBuilder],
                          ctx: Dict[str, Any], out: List[str]) -> None:
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
            if chunk is not None:
                usage_meta = getattr(chunk, "usage_metadata", None)
                if usage_meta:
                    self._accumulate_usage(ctx, item.get("run_id"), usage_meta, out)
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
                self._accumulate_usage(ctx, item.get("run_id"), usage_meta, out)
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
        clean_output = _TOOL_EXIT_SUFFIX.sub("", _tool_output_value(raw_output)) if raw_output else ""
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
        is_error = failure is not None or output_status == "error"
        err_fields = failure_to_sse_error_fields(failure) if failure else {}
        err_s = err_fields.get("error") if is_error else None
        err_cat = err_fields.get("errorCategory") if is_error else None
        if is_error and not err_s:
            err_s = sanitize_tool_error(clean_output)
        sse_status = "error" if is_error else "success"
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
            )

        if tool_name == TASK_TOOL_NAME:
            self._on_task_tool_end(tool_call_id, ctx)

        part_id = self._tool_part_ids.get(tool_call_id) or _new_id("part-tool")
        self._emit_tool_output(
            out, part_id, tool_call_id, display_output, sse_status, err_s, duration_ms,
            error_category=err_cat,
        )

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
            )
            if not ok:
                builder.append_text(err_s)

        if tool_name == TASK_TOOL_NAME:
            self._on_task_tool_end(tool_call_id, ctx)

        part_id = self._tool_part_ids.get(tool_call_id) or _new_id("part-tool")
        self._emit_tool_output(
            out, part_id, tool_call_id, "", "error", err_s, duration_ms,
            error_category=err_cat,
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
