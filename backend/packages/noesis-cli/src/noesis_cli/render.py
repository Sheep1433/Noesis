"""Event rendering for Noesis CLI — real-time terminal output + eval collector.

Independent of server: extracts reasoning/text from LangChain chunks
directly, aligned with noesis/chat/event_mapping/reasoning.py but
without importing it (CLI only depends on noesis-core).
"""

from __future__ import annotations

from typing import Any

from rich.console import Console


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    s = value if isinstance(value, str) else str(value)
    return s if s else None


def extract_reasoning(chunk: Any) -> str | None:
    """从 AIMessageChunk 提取思考增量（对齐 reasoning.extract_reasoning_delta）。"""
    if chunk is None:
        return None
    kwargs = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(kwargs, dict):
        for key in ("reasoning_content", "reasoning"):
            delta = _coerce_str(kwargs.get(key))
            if delta:
                return delta
    return _coerce_str(getattr(chunk, "reasoning_content", None))


def extract_text(chunk: Any) -> str:
    """从 AIMessageChunk 提取可见正文（对齐 reasoning.extract_text_content）。"""
    if chunk is None:
        return ""
    content = getattr(chunk, "content", None)
    if content is None and isinstance(chunk, dict):
        content = chunk.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    if content:
        return str(content)
    return ""


def _truncate(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


class StreamRenderer:
    """实时流式渲染：chat 命令用。"""

    def __init__(self, console: Console):
        self.console = console
        self._in_text = False
        self._in_reasoning = False
        self._input_tokens = 0
        self._output_tokens = 0

    def consume(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        event_name = event.get("event")

        if event_type == "__tw_finish__":
            self._close()
            reason = event.get("finish_reason") or "stop"
            usage = event.get("usage") or {}
            if isinstance(usage, dict):
                self._input_tokens += int(usage.get("input_tokens") or 0)
                self._output_tokens += int(usage.get("output_tokens") or 0)
            tag = "" if reason in ("stop", "completed") else f" [{reason}]"
            self.console.print(
                f"[dim]tokens: ↑{self._input_tokens} ↓{self._output_tokens}{tag}[/]"
            )
            return

        if event_type in ("__tw_error__", "__tw_abort__", "abort"):
            self._close()
            err = event.get("content") or event.get("error") or "error"
            self.console.print(f"[red]✗ {err}[/]")
            return

        if event_name == "on_chat_model_stream":
            chunk = (event.get("data") or {}).get("chunk")
            reasoning = extract_reasoning(chunk)
            if reasoning:
                self._close_text()
                if not self._in_reasoning:
                    self._in_reasoning = True
                self.console.print(f"[dim]{reasoning}[/]", end="")
            text = extract_text(chunk)
            if text:
                self._close_reasoning()
                if not self._in_text:
                    self._in_text = True
                self.console.print(text, end="")
            return

        if event_name == "on_chat_model_end":
            output = (event.get("data") or {}).get("output")
            usage = getattr(output, "usage_metadata", None) if output else None
            if isinstance(usage, dict):
                self._input_tokens += int(usage.get("input_tokens") or 0)
                self._output_tokens += int(usage.get("output_tokens") or 0)
            return

        if event_name == "on_tool_start":
            self._close()
            name = event.get("name") or "unknown"
            inp = (event.get("data") or {}).get("input")
            inp_str = _truncate(str(inp), 200) if inp else ""
            self.console.print(f"[bold yellow]⚙ {name}[/][dim] {inp_str}[/]")
            return

        if event_name == "on_tool_end":
            output = (event.get("data") or {}).get("output")
            content = getattr(output, "content", str(output)) if output else ""
            self.console.print(f"  [green]✓ {_truncate(str(content), 300)}[/]")
            return

        if event_name == "on_tool_error":
            err = str((event.get("data") or {}).get("error") or "tool error")
            self.console.print(f"  [red]✗ {_truncate(err, 300)}[/]")
            return

    def _close_text(self) -> None:
        if self._in_text:
            self.console.print()
            self._in_text = False

    def _close_reasoning(self) -> None:
        if self._in_reasoning:
            self.console.print()
            self._in_reasoning = False

    def _close(self) -> None:
        self._close_reasoning()
        self._close_text()

    def end_turn(self) -> None:
        self._close()


class EvalCollector:
    """评测收集器：eval 命令用。对齐 evals.agent.runtime.AgentEventCollector。"""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.tool_stats: dict[str, int] = {}
        self.tool_outputs: list[dict] = []
        self.completed = False
        self.finish_reason: str | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.error: str | None = None
        self._pending_tools: dict[str, str] = {}

    def consume(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        event_name = event.get("event")

        if event_type == "__tw_finish__":
            self.finish_reason = event.get("finish_reason") or None
            self.completed = self.error is None and self.finish_reason in (None, "stop", "completed")
            usage = event.get("usage") or {}
            if isinstance(usage, dict):
                self.input_tokens += int(usage.get("input_tokens") or 0)
                self.output_tokens += int(usage.get("output_tokens") or 0)
            return
        if event_type in ("__tw_error__", "__tw_abort__", "abort"):
            self.error = str(event.get("content") or event.get("error") or "agent error")
            self.completed = False
            return
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
            content = getattr(output, "content", str(output)) if output else ""
            self.tool_outputs.append({"name": name, "output": str(content)})
            return
        if event_name == "on_chat_model_stream":
            chunk = (event.get("data") or {}).get("chunk")
            text = extract_text(chunk)
            if text:
                self.text_parts.append(text)
            return
        if event_name == "on_chat_model_end":
            output = (event.get("data") or {}).get("output")
            usage = getattr(output, "usage_metadata", None) if output else None
            if isinstance(usage, dict):
                self.input_tokens += int(usage.get("input_tokens") or 0)
                self.output_tokens += int(usage.get("output_tokens") or 0)
            return

    @property
    def final_text(self) -> str:
        return "".join(self.text_parts).strip()

    def to_dict(self, *, query: str, model: str | None, latency_ms: int) -> dict:
        return {
            "query": query,
            "model": model,
            "completed": self.completed,
            "finish_reason": self.finish_reason,
            "latency_ms": latency_ms,
            "final_text": self.final_text,
            "tool_stats": dict(self.tool_stats),
            "tool_outputs": list(self.tool_outputs),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error": self.error,
        }
