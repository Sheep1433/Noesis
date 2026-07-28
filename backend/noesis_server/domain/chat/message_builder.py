"""
消息内容构建工具

multipart 消息格式：
- TextPart: 文本回复
- ReasoningPart: 推理 / 思考过程
- ToolPart: 工具调用 + 输出

AssistantMessageBuilder 累积一轮 assistant 消息的 parts，结束时序列化为 JSON 落库。
UserMessageBuilder 用于构造 user 消息（仅含 text）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MessagePart:
    type: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _part_parent_fields(parent_task_call_id: Optional[str]) -> Dict[str, Any]:
    if not parent_task_call_id:
        return {}
    return {"parent_task_call_id": parent_task_call_id}


@dataclass
class TextPart(MessagePart):
    content: str = ""
    parent_task_call_id: Optional[str] = None
    type: str = "text"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "content": self.content}
        out.update(_part_parent_fields(self.parent_task_call_id))
        return out


@dataclass
class ReasoningPart(MessagePart):
    content: str = ""
    parent_task_call_id: Optional[str] = None
    type: str = "reasoning"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "content": self.content}
        out.update(_part_parent_fields(self.parent_task_call_id))
        return out


@dataclass
class ToolPart(MessagePart):
    name: str = ""
    arguments: Optional[Dict[str, Any]] = None
    output: Optional[str] = None
    tool_call_id: Optional[str] = None
    duration_ms: Optional[int] = None
    parent_task_call_id: Optional[str] = None
    status: str = "running"
    error: Optional[str] = None
    error_category: Optional[str] = None
    hitl: Optional[Dict[str, Any]] = None
    outcome: Optional[str] = None
    type: str = "tool"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "name": self.name,
            "input": self.arguments if self.arguments is not None else {},
            "output": self.output,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
        }
        if self.duration_ms is not None:
            out["duration_ms"] = self.duration_ms
        if self.error:
            out["error"] = self.error
        if self.error_category:
            out["errorCategory"] = self.error_category
        if self.parent_task_call_id:
            out["parent_task_call_id"] = self.parent_task_call_id
        if self.hitl:
            out["hitl"] = self.hitl
        if self.outcome:
            out["outcome"] = self.outcome
        return out


def _part_from_dict(data: Dict[str, Any]) -> MessagePart:
    part_type = data.get("type")
    parent = data.get("parent_task_call_id")
    if part_type == "text":
        return TextPart(content=data.get("content", ""), parent_task_call_id=parent)
    if part_type == "reasoning":
        return ReasoningPart(content=data.get("content", ""), parent_task_call_id=parent)
    if part_type == "tool":
        inp = data.get("input")
        if inp is None and "arguments" in data:
            inp = data.get("arguments")
        return ToolPart(
            name=data.get("name") or "",
            arguments=inp if inp is not None else {},
            output=data.get("output"),
            tool_call_id=data.get("tool_call_id"),
            duration_ms=data.get("duration_ms"),
            parent_task_call_id=parent,
            status=data.get("status") or "running",
            error=data.get("error"),
            error_category=data.get("errorCategory"),
            hitl=data.get("hitl"),
            outcome=data.get("outcome"),
        )
    raise ValueError(f"Unknown part type: {part_type}")


@dataclass
class MessageContent:
    parts: List[MessagePart] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parts": [p.to_dict() if isinstance(p, MessagePart) else p for p in self.parts]
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def is_empty(self) -> bool:
        return len(self.parts) == 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageContent":
        parts: List[MessagePart] = []
        for raw in data.get("parts", []):
            if isinstance(raw, dict):
                parts.append(_part_from_dict(raw))
            else:
                parts.append(raw)
        return cls(parts=parts)

    @classmethod
    def from_json(cls, json_str: str) -> "MessageContent":
        if not json_str:
            return cls()
        return cls.from_dict(json.loads(json_str))


class AssistantMessageBuilder:
    """累积 text / reasoning / tool parts，并按 tool_call_id 索引以支持并行 / 乱序工具调用。"""

    def __init__(self, session_id: str = "", message_id: str = ""):
        self.session_id = session_id
        self.message_id = message_id
        self._content = MessageContent()
        self._tools_by_call_id: Dict[str, ToolPart] = {}
        self._last_tool: Optional[ToolPart] = None

    def append_text(self, text: str, parent_task_call_id: Optional[str] = None) -> None:
        self._content.parts.append(
            TextPart(content=text, parent_task_call_id=parent_task_call_id),
        )

    def append_text_delta(
        self,
        text: str,
        parent_task_call_id: Optional[str] = None,
    ) -> None:
        """流式正文增量：合并进同 parent 最近 text part（跳过其它 parent 交错）。"""
        if not text:
            return
        for part in reversed(self._content.parts):
            if part.parent_task_call_id != parent_task_call_id:
                continue
            if isinstance(part, TextPart):
                part.content = (part.content or "") + text
                return
            break
        self._content.parts.append(
            TextPart(content=text, parent_task_call_id=parent_task_call_id),
        )

    def append_reasoning(self, reasoning: str, parent_task_call_id: Optional[str] = None) -> None:
        self._content.parts.append(
            ReasoningPart(content=reasoning, parent_task_call_id=parent_task_call_id),
        )

    def append_reasoning_delta(
        self,
        reasoning: str,
        parent_task_call_id: Optional[str] = None,
    ) -> None:
        """流式思考增量：合并进同 parent 最近 reasoning（跳过其它 parent 交错）。"""
        if not reasoning:
            return
        for part in reversed(self._content.parts):
            if part.parent_task_call_id != parent_task_call_id:
                continue
            if isinstance(part, ReasoningPart):
                part.content = (part.content or "") + reasoning
                return
            break
        self._content.parts.append(
            ReasoningPart(content=reasoning, parent_task_call_id=parent_task_call_id),
        )

    def append_tool(
        self,
        tool: str,
        tool_input: Optional[Dict[str, Any]] = None,
        tool_call_id: Optional[str] = None,
        parent_task_call_id: Optional[str] = None,
        *,
        status: str = "running",
        hitl: Optional[Dict[str, Any]] = None,
    ) -> None:
        if tool_call_id:
            existing = self._tools_by_call_id.get(tool_call_id)
            if existing is not None:
                existing.name = tool or existing.name
                existing.arguments = tool_input if tool_input is not None else existing.arguments
                if parent_task_call_id:
                    existing.parent_task_call_id = parent_task_call_id
                if hitl:
                    existing.hitl = {**(existing.hitl or {}), **hitl}
                # 重放 tool start 只补充同一块的输入信息，不能把已有结果退回 running。
                if existing.status == "running":
                    existing.status = status
                    self._last_tool = existing
                return
        part = ToolPart(
            name=tool,
            arguments=tool_input,
            tool_call_id=tool_call_id,
            parent_task_call_id=parent_task_call_id,
            status=status,
            hitl=hitl,
        )
        self._content.parts.append(part)
        self._last_tool = part
        if tool_call_id:
            self._tools_by_call_id[tool_call_id] = part

    def resolve_hitl_tool_call_id(
        self,
        tool: str,
        tool_input: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """将 HITL resume 的 callback run UUID 映射回模型原始 tool_call_id。"""
        candidates = [
            part.tool_call_id
            for part in self._content.parts
            if isinstance(part, ToolPart)
            and part.tool_call_id
            and part.name == tool
            and (part.arguments or {}) == (tool_input or {})
            and part.status == "running"
            and isinstance(part.hitl, dict)
            and part.hitl.get("status") in {"pending", "approved", "answered"}
        ]
        return candidates[0] if len(candidates) == 1 else None

    def load_from_content_dict(self, data: Dict[str, Any]) -> None:
        """从已落库 content 恢复 parts（HITL resume 续写同一 assistant 行）。"""
        self._content = MessageContent.from_dict(data or {"parts": []})
        self._tools_by_call_id = {}
        self._last_tool = None
        canonical_parts: List[MessagePart] = []
        for part in self._content.parts:
            if isinstance(part, ToolPart) and part.tool_call_id:
                existing = self._tools_by_call_id.get(part.tool_call_id)
                if existing is not None:
                    existing.name = part.name or existing.name
                    existing.arguments = part.arguments or existing.arguments
                    existing.output = part.output if part.output is not None else existing.output
                    existing.status = part.status or existing.status
                    existing.error = part.error or existing.error
                    existing.error_category = part.error_category or existing.error_category
                    existing.duration_ms = part.duration_ms or existing.duration_ms
                    existing.parent_task_call_id = (
                        part.parent_task_call_id or existing.parent_task_call_id
                    )
                    existing.hitl = {**(existing.hitl or {}), **(part.hitl or {})} or None
                    existing.outcome = part.outcome or existing.outcome
                    continue
                self._tools_by_call_id[part.tool_call_id] = part
                if part.status == "running":
                    self._last_tool = part
            canonical_parts.append(part)
        self._content.parts = canonical_parts

    def update_tool_hitl(
        self,
        tool_call_id: Optional[str],
        hitl: Dict[str, Any],
        *,
        status: Optional[str] = None,
    ) -> None:
        if not tool_call_id:
            return
        target = self._tools_by_call_id.get(tool_call_id)
        if target is None:
            return
        target.hitl = {**(target.hitl or {}), **hitl}
        if status is not None:
            target.status = status

    def append_tool_output(
        self,
        tool: str,
        output: str,
        tool_call_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
        *,
        status: str = "success",
        error: Optional[str] = None,
        error_category: Optional[str] = None,
    ) -> None:
        """优先按 tool_call_id 定位（支持并行 / 乱序），否则回退到最近一次 append_tool。"""
        target = (
            self._tools_by_call_id.get(tool_call_id)
            if tool_call_id
            else None
        ) or self._last_tool

        if target is None:
            raise ValueError(
                f"append_tool_output without matching tool: tool={tool}, tool_call_id={tool_call_id}"
            )

        target.output = output
        target.status = status
        target.error = error
        target.error_category = error_category
        if duration_ms is not None:
            target.duration_ms = duration_ms
        if tool_call_id:
            target.tool_call_id = tool_call_id
        if target is self._last_tool:
            self._last_tool = None

    def mark_running_tools_unknown(self, message: str) -> int:
        """停止/重启时不推断远程副作用，收口所有未完成工具。"""
        count = 0
        for part in self._content.parts:
            if not isinstance(part, ToolPart) or part.status != "running":
                continue
            part.status = "error"
            part.outcome = "unknown"
            part.error = message
            part.error_category = "unknown"
            if part.output is None:
                part.output = message
            count += 1
        self._last_tool = None
        return count

    def to_dict(self) -> Dict[str, Any]:
        return self._content.to_dict()

    def serialize(self) -> str:
        return self._content.to_json()

    def is_empty(self) -> bool:
        return self._content.is_empty()


class UserMessageBuilder:
    """User 消息只含一个 text part。"""

    def __init__(self, content: str = ""):
        self._content = MessageContent()
        if content:
            self._content.parts.append(TextPart(content=content))

    def to_dict(self) -> Dict[str, Any]:
        return self._content.to_dict()

    def serialize(self) -> str:
        return self._content.to_json()
