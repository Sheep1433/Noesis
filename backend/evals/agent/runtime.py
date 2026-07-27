"""Shared event collection and result schema for Harness-based Agent evaluations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return ""
    content = getattr(value, "content", value)
    return content if isinstance(content, str) else str(content)


@dataclass
class AgentRunResult:
    run_id: str
    suite: str
    subject: str
    model: str | None = None
    completed: bool = False
    finish_reason: str | None = None
    latency_ms: int = 0
    final_text: str = ""
    tool_stats: dict[str, int] = field(default_factory=dict)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_manifest(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload["schema_version"] = "noesis-eval-run/v1"
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        return payload


@dataclass
class AgentEventCollector:
    text_parts: list[str] = field(default_factory=list)
    tool_stats: dict[str, int] = field(default_factory=dict)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False
    finish_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    _pending_tools: dict[str, str] = field(default_factory=dict)

    def consume(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "__tw_finish__":
            self.finish_reason = str(event.get("finish_reason") or "") or None
            self.completed = self.finish_reason == "stop"
            return
        if event_type in {"__tw_error__", "abort", "__tw_abort__"}:
            self.error = str(event.get("content") or "agent error")
            self.completed = False
            return

        event_name = event.get("event")
        if event_name == "on_tool_start":
            name = str(event.get("name") or "unknown")
            run_id = str(event.get("run_id") or f"tool-{len(self.tool_outputs)}")
            self.tool_stats[name] = self.tool_stats.get(name, 0) + 1
            self._pending_tools[run_id] = name
            return
        if event_name == "on_tool_end":
            run_id = str(event.get("run_id") or "")
            name = self._pending_tools.pop(run_id, str(event.get("name") or "unknown"))
            output = (event.get("data") or {}).get("output")
            self.tool_outputs.append({"name": name, "output": _text(output)})
            return
        if event_name == "on_chat_model_stream":
            chunk = (event.get("data") or {}).get("chunk")
            content = getattr(chunk, "content", None) if chunk is not None else None
            if content:
                self.text_parts.append(_text(content))
            return
        if event_name == "on_chat_model_end":
            output = (event.get("data") or {}).get("output")
            usage = getattr(output, "usage_metadata", None) if output is not None else None
            if isinstance(usage, dict):
                self.input_tokens += int(usage.get("input_tokens") or 0)
                self.output_tokens += int(usage.get("output_tokens") or 0)
            elif usage:
                self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

    @property
    def final_text(self) -> str:
        return "".join(self.text_parts).strip()

    def result(
        self,
        *,
        run_id: str,
        suite: str,
        subject: str,
        latency_ms: int,
        model: str | None = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=run_id,
            suite=suite,
            subject=subject,
            model=model,
            completed=self.completed,
            finish_reason=self.finish_reason,
            latency_ms=latency_ms,
            final_text=self.final_text,
            tool_stats=dict(self.tool_stats),
            tool_outputs=list(self.tool_outputs),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            error=self.error,
        )
