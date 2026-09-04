"""工具输入/输出的 SSE 载荷转换：JSON-safe 归一、展示截断、结果元数据提取。

从 LangGraphSseBridge 拆出的无状态纯函数——只做转换，不持有 ctx/builder
状态。bridge 与 builder 层共用这里的归一结果，保证模型侧原始工具结果与
发往 UI 的展示载荷一致。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Optional, Tuple

from langgraph.types import Command

from noesis.chat.tool_state import extract_process_result

TOOL_EXIT_PROTOCOL_RE = re.compile(
    r"\s*\[Command (?P<result>succeeded|failed) with exit code (?P<exit_code>\d+)\]"
    r"(?P<truncated>\s*\[Output was truncated due to size limits\])?\s*$"
)
TOOL_INPUT_MAX = 65536
TOOL_OUTPUT_DISPLAY_MAX = 24000

_OMIT_NON_JSON_TOOL_INPUT = object()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


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


def normalize_tool_input(raw: Any) -> Tuple[Dict[str, Any], Optional[str]]:
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
    if len(dumped) > TOOL_INPUT_MAX:
        dumped = f"{dumped[:TOOL_INPUT_MAX]}..."
    return {"_tw_tool_input": safe_raw}, dumped


def tool_output_value(raw_out: Any) -> str:
    if raw_out is None:
        return ""
    if isinstance(raw_out, Command):
        # 工具以 Command 返回（如 start_task 携带 bg_tasks 身份 update）时，
        # 模型可见文本在 update.messages 的 ToolMessage 里；str(Command) repr
        # 对模型与 UI 都不是合法输出（曾整段入库成为工具结果展示）
        for message in (raw_out.update or {}).get("messages") or []:
            content = getattr(message, "content", None)
            if isinstance(content, str) and content:
                return content
        return ""
    return raw_out.content if hasattr(raw_out, "content") else str(raw_out)


def bound_tool_output_for_display(value: str) -> Tuple[str, bool]:
    """限制发往 UI 的单次工具输出，不影响模型侧原始工具结果。"""
    if len(value) <= TOOL_OUTPUT_DISPLAY_MAX:
        return value, False
    return (
        f"{value[:TOOL_OUTPUT_DISPLAY_MAX]}\n\n"
        f"…（工具输出过长，已截断展示）",
        True,
    )


def retrieval_payload(raw: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("results"), list):
        return None
    return parsed


def extract_tool_result(raw_out: Any, content: str) -> Tuple[str, Dict[str, Any]]:
    """Extract explicit result metadata; DeepAgents' anchored suffix is its wire protocol."""
    metadata = extract_process_result(raw_out)
    artifact = getattr(raw_out, "artifact", None)
    if artifact is not None:
        metadata.update(extract_process_result(artifact))
    match = TOOL_EXIT_PROTOCOL_RE.search(content)
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


def resolve_tool_call_id(item: Dict[str, Any], data: Dict[str, Any]) -> str:
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
    return new_id("tool")


def resolve_tool_output_call_id(
    item: Dict[str, Any],
    data: Dict[str, Any],
    ctx: Dict[str, Any],
    tool_part_ids: Dict[str, str],
) -> str:
    """tool-output / tool-error 与 tool-input 对齐。"""
    run_id = item.get("run_id")
    if run_id:
        mapped = (ctx.get("run_id_to_tool_call_id") or {}).get(str(run_id))
        if mapped and mapped in tool_part_ids:
            return mapped
    resolved = resolve_tool_call_id(item, data)
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
