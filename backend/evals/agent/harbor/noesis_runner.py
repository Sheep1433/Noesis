"""Noesis Harbor Agent 共用运行逻辑（prompt / collector / LLM 解析）。"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.prompts.execution import build_execution_sections
from config.env import ModelConfig
from llm import get_llm
from llm.factory import _build_chat_model

_AGENT_VERSION = "0.1.0"
_DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"


def build_harbor_system_prompt(*, working_dir: str) -> str:
    sections = [
        "<role>",
        "你是终端任务智能体，在隔离的 Linux 容器内完成用户指令。",
        "使用 ls、read_file、write_file、edit_file、execute、grep、glob 等工具真实操作容器文件系统。",
        "</role>",
        "<environment>",
        f"- 容器工作目录（execute 默认 cwd）：`{working_dir}`",
        "- 文件路径须为**绝对路径**（以 `/` 开头）；先用 `ls /` 或 `ls "
        f"{working_dir}` 熟悉目录结构。",
        "- 需要切换目录时在同一 command 内用 `&&` 链接（如 "
        f"`cd {working_dir} && make`）。",
        "- 交付前用命令或 read_file 验证结果，不要只描述计划。",
        "</environment>",
        *build_execution_sections(include_tool_enforcement=True),
    ]
    return "\n\n".join(sections)


def resolve_harbor_llm(model_name: str | None):
    normalized = (model_name or "").strip()
    if normalized and "/" in normalized:
        provider, raw_name = normalized.split("/", maxsplit=1)
        if provider == "opencode":
            api_key = os.getenv("OPENCODE_API_KEY", "public")
            base_url = os.getenv("OPENCODE_API_BASE", "").strip() or ModelConfig.model_base_url
            temperature = float(os.getenv("HARBOR_NOESIS_TEMPERATURE", "0") or 0)
            return _build_chat_model(
                model_type="opencode",
                model_name=raw_name,
                temperature=temperature,
                model_base_url=base_url,
                model_api_key=api_key,
            )
    if normalized:
        from llm.catalog import get_model_catalog

        catalog_ids = {entry.id for entry in get_model_catalog()}
        if normalized in catalog_ids:
            return get_llm(model_id=normalized)
    return get_llm()


@dataclass
class HarborRunCollector:
    instruction: str
    model_name: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    tool_stats: dict[str, int] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    _step_id: int = 1
    _pending_tools: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def add_user_step(self) -> None:
        self.steps.append(
            {
                "step_id": self._step_id,
                "timestamp": self._now_iso(),
                "source": "user",
                "message": self.instruction,
            }
        )
        self._step_id += 1

    def consume(self, event: dict[str, Any]) -> None:
        event_name = event.get("event")
        if event_name == "on_tool_start":
            tool_name = str(event.get("name") or "unknown")
            self.tool_stats[tool_name] = self.tool_stats.get(tool_name, 0) + 1
            run_id = str(event.get("run_id") or uuid.uuid4())
            tool_input = event.get("data", {}).get("input") or {}
            self._pending_tools[run_id] = {
                "name": tool_name,
                "input": tool_input,
            }
            return

        if event_name == "on_tool_end":
            run_id = str(event.get("run_id") or "")
            pending = self._pending_tools.pop(run_id, None)
            if pending is None:
                return
            output = event.get("data", {}).get("output")
            output_text = str(output) if output is not None else ""
            tool_call_id = f"call_{self._step_id}"
            self.steps.append(
                {
                    "step_id": self._step_id,
                    "timestamp": self._now_iso(),
                    "source": "agent",
                    "model_name": self.model_name,
                    "tool_calls": [
                        {
                            "tool_call_id": tool_call_id,
                            "function_name": str(pending["name"]),
                            "arguments": pending["input"]
                            if isinstance(pending["input"], dict)
                            else {"input": pending["input"]},
                        }
                    ],
                    "observation": {
                        "results": [
                            {
                                "content": output_text[:20000],
                                "source_call_id": tool_call_id,
                            }
                        ]
                    },
                }
            )
            self._step_id += 1
            return

        if event_name == "on_chat_model_stream":
            data = event.get("data") or {}
            chunk = data.get("chunk")
            if chunk is None:
                return
            content = getattr(chunk, "content", None)
            if content:
                self.text_parts.append(str(content))
            return

        if event_name == "on_chat_model_end":
            data = event.get("data") or {}
            output = data.get("output")
            usage = getattr(output, "usage_metadata", None) if output is not None else None
            if usage:
                self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

    @property
    def final_text(self) -> str:
        return "".join(self.text_parts).strip()

    def finalize_agent_message(self) -> None:
        if not self.final_text:
            return
        self.steps.append(
            {
                "step_id": self._step_id,
                "timestamp": self._now_iso(),
                "source": "agent",
                "model_name": self.model_name,
                "message": self.final_text,
            }
        )
        self._step_id += 1

    def to_trajectory_dict(self, session_id: str) -> dict[str, Any]:
        self.finalize_agent_message()
        if not self.steps:
            self.add_user_step()
        final_metrics: dict[str, Any] = {}
        if self.input_tokens:
            final_metrics["total_prompt_tokens"] = self.input_tokens
        if self.output_tokens:
            final_metrics["total_completion_tokens"] = self.output_tokens
        return {
            "schema_version": "ATIF-v1.7",
            "session_id": session_id,
            "agent": {
                "name": "noesis-harbor",
                "version": _AGENT_VERSION,
                "model_name": self.model_name,
            },
            "steps": self.steps,
            "final_metrics": final_metrics or None,
            "notes": "Noesis Harbor adapter (create_noesis_agent + container proxy)",
        }


def write_run_artifacts(
    *,
    logs_dir: Path,
    session_id: str,
    collector: HarborRunCollector,
) -> None:
    trajectory = collector.to_trajectory_dict(session_id)
    (logs_dir / "trajectory.json").write_text(
        json.dumps(trajectory, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = {
        "session_id": session_id,
        "model": collector.model_name,
        "final_text": collector.final_text,
        "tool_stats": collector.tool_stats,
        "tokens": {
            "input": collector.input_tokens,
            "output": collector.output_tokens,
        },
        "error": collector.error,
    }
    (logs_dir / "noesis.txt").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


