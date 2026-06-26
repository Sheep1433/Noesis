"""SSE 流消费与指标采集（Locust 深度研究压测共用）。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


_TOKEN_EVENTS = frozenset({"text-delta", "reasoning-delta"})
_TOOL_EVENTS = frozenset({"tool-input-start", "tool-call-start", "tool-output-available"})


@dataclass
class SseStreamMetrics:
    ttft_ms: Optional[float] = None
    total_ms: Optional[float] = None
    finish_reason: Optional[str] = None
    error_message: Optional[str] = None
    event_counts: dict[str, int] = field(default_factory=dict)
    bytes_received: int = 0

    @property
    def succeeded(self) -> bool:
        return self.error_message is None and self.finish_reason == "stop"

    @property
    def tool_calls(self) -> int:
        return sum(self.event_counts.get(name, 0) for name in _TOOL_EVENTS)


def _parse_sse_frame(frame: str) -> tuple[Optional[str], Optional[str]]:
    event_name: Optional[str] = None
    data_line: Optional[str] = None
    for line in frame.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_line = line[len("data:") :].strip()
    return event_name, data_line


def consume_sse_stream(
    line_iter: Iterable[str],
    *,
    started_at: Optional[float] = None,
    deadline: Optional[float] = None,
) -> SseStreamMetrics:
    """逐行读取 SSE 响应，直到 [DONE] 或 finish/error/abort。"""
    metrics = SseStreamMetrics()
    started_at = started_at or time.perf_counter()
    buffer = ""

    def _record_event(name: str) -> None:
        metrics.event_counts[name] = metrics.event_counts.get(name, 0) + 1

    def _handle_data(event_name: Optional[str], data_line: str) -> bool:
        if data_line == "[DONE]":
            metrics.total_ms = (time.perf_counter() - started_at) * 1000
            return True

        payload: dict[str, Any]
        try:
            payload = json.loads(data_line)
        except json.JSONDecodeError:
            return False

        event_type = event_name or str(payload.get("type") or "")
        if event_type:
            _record_event(event_type)

        if event_type in _TOKEN_EVENTS and metrics.ttft_ms is None:
            metrics.ttft_ms = (time.perf_counter() - started_at) * 1000

        if event_type == "finish":
            metrics.finish_reason = str(payload.get("finish_reason") or "")
            metrics.total_ms = (time.perf_counter() - started_at) * 1000
            if metrics.finish_reason != "stop":
                metrics.error_message = str(payload.get("error") or metrics.finish_reason)
            return True

        if event_type == "error":
            metrics.error_message = str(payload.get("error") or payload.get("message") or "stream error")
            metrics.total_ms = (time.perf_counter() - started_at) * 1000
            return True

        if event_type == "abort":
            metrics.error_message = str(payload.get("content") or "aborted")
            metrics.total_ms = (time.perf_counter() - started_at) * 1000
            return True

        return False

    for raw_line in line_iter:
        if deadline is not None and time.perf_counter() >= deadline:
            metrics.error_message = "client timeout while reading SSE"
            metrics.total_ms = (time.perf_counter() - started_at) * 1000
            return metrics

        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
        metrics.bytes_received += len(line.encode("utf-8")) + 1

        if line == "":
            if buffer:
                event_name, data_line = _parse_sse_frame(buffer)
                buffer = ""
                if data_line is not None and _handle_data(event_name, data_line):
                    return metrics
            continue

        buffer = f"{buffer}\n{line}" if buffer else line

    if buffer:
        event_name, data_line = _parse_sse_frame(buffer)
        if data_line is not None:
            _handle_data(event_name, data_line)

    metrics.total_ms = (time.perf_counter() - started_at) * 1000
    if metrics.error_message is None and metrics.finish_reason != "stop":
        metrics.error_message = metrics.finish_reason or "stream ended without finish"
    return metrics
