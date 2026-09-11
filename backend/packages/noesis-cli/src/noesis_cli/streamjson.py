"""CLI print 模式的 stream-json 契约：事件序列化、收集、空收场守卫。

契约形状与 Agent 内部事件对齐（finish/error/tool/text/usage），载荷全部
JSON 可序列化；evals 的 cli_driver 复用本模块的收集器与守卫，保证 CLI
输出与评测解析单一来源。
"""

from __future__ import annotations

import json
from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return ""
    content = getattr(value, "content", value)
    return content if isinstance(content, str) else str(content)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def event_to_stream_line(event: dict[str, Any]) -> dict[str, Any] | None:
    """Agent 内部事件 → 单行 JSON 载荷；不关心的事件返回 None。"""
    event_type = str(event.get("type") or "")
    event_name = event.get("event")

    if event_type == "__tw_finish__":
        return {
            "type": "__tw_finish__",
            "finish_reason": event.get("finish_reason"),
            "usage": _usage_dict(event.get("usage")) or {"input_tokens": 0, "output_tokens": 0},
        }
    if event_type in ("__tw_error__", "abort", "__tw_abort__"):
        return {"type": "__tw_error__", "content": str(event.get("content") or "agent error")}
    if event_name == "on_tool_start":
        return {
            "event": "on_tool_start",
            "name": str(event.get("name") or "unknown"),
            "run_id": str(event.get("run_id") or ""),
            "data": {"input": _jsonable((event.get("data") or {}).get("input"))},
        }
    if event_name == "on_tool_end":
        return {
            "event": "on_tool_end",
            "name": str(event.get("name") or "unknown"),
            "run_id": str(event.get("run_id") or ""),
            "data": {"output": _text((event.get("data") or {}).get("output"))},
        }
    if event_name == "on_chat_model_stream":
        chunk = (event.get("data") or {}).get("chunk")
        text = _text(chunk)
        return {"event": "on_chat_model_stream", "data": {"text": text}} if text else None
    if event_name == "on_chat_model_end":
        output = event.get("data", {}).get("output")
        usage = _usage_dict(getattr(output, "usage_metadata", None))
        return {"event": "on_chat_model_end", "data": {"usage": usage}} if usage else None
    return None


class StreamCollector:
    """消费 stream-json 行，产出与 evals 评测记录同形的 dict。"""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.tool_stats: dict[str, int] = {}
        self.tool_outputs: list[dict[str, Any]] = []
        self.completed: bool = False
        self.finish_reason: str | None = None
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.error: str | None = None
        self._pending_tools: dict[str, tuple[str, Any]] = {}

    def consume(self, line_obj: dict[str, Any]) -> None:
        event_type = str(line_obj.get("type") or "")
        event_name = line_obj.get("event")

        if event_type == "__tw_finish__":
            self.finish_reason = str(line_obj.get("finish_reason") or "") or None
            self.completed = self.error is None and self.finish_reason in (None, "stop", "completed")
            return
        if event_type == "__tw_error__":
            self.error = str(line_obj.get("content") or "agent error")
            self.completed = False
            return
        if event_name == "on_tool_start":
            name = str(line_obj.get("name") or "unknown")
            run_id = str(line_obj.get("run_id") or f"tool-{len(self.tool_outputs)}")
            self.tool_stats[name] = self.tool_stats.get(name, 0) + 1
            self._pending_tools[run_id] = (name, (line_obj.get("data") or {}).get("input"))
            return
        if event_name == "on_tool_end":
            run_id = str(line_obj.get("run_id") or "")
            name, tool_input = self._pending_tools.pop(
                run_id, (str(line_obj.get("name") or "unknown"), None))
            self.tool_outputs.append({
                "name": name,
                "input": tool_input,
                "output": str((line_obj.get("data") or {}).get("output") or ""),
            })
            return
        if event_name == "on_chat_model_stream":
            self.text_parts.append(str((line_obj.get("data") or {}).get("text") or ""))
            return
        if event_name == "on_chat_model_end":
            usage = (line_obj.get("data") or {}).get("usage")
            if isinstance(usage, dict):
                self.input_tokens += int(usage.get("input_tokens") or 0)
                self.output_tokens += int(usage.get("output_tokens") or 0)

    @property
    def final_text(self) -> str:
        return "".join(self.text_parts).strip()

    def to_record(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "finish_reason": self.finish_reason,
            "final_text": self.final_text,
            "tool_stats": dict(self.tool_stats),
            "tool_outputs": list(self.tool_outputs),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error": self.error,
        }


def flag_empty_completion(record: dict[str, Any]) -> dict[str, Any]:
    """completed 但零文本零工具输出 → 改判失败。

    上游模型故障（如网关欠费 402）经错误中间件转成空收场；真实作答
    不可能文本与工具全空，不拦下就会当「跑通」落盘。
    """
    if (
        record.get("completed")
        and not str(record.get("final_text") or "").strip()
        and not (record.get("tool_stats") or {})
    ):
        record["completed"] = False
        record["error"] = (
            "empty completion: 无文本无工具输出（疑似上游模型故障，"
            "查后端日志与网关余额）"
        )
    return record
