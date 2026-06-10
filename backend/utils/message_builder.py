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
    type: str = "tool"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "name": self.name,
            "input": self.arguments,
            "output": self.output,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
        }
        if self.duration_ms is not None:
            out["duration_ms"] = self.duration_ms
        if self.error:
            out["error"] = self.error
        if self.parent_task_call_id:
            out["parent_task_call_id"] = self.parent_task_call_id
        return out


def _part_from_dict(data: Dict[str, Any]) -> MessagePart:
    part_type = data.get("type")
    parent = data.get("parent_task_call_id")
    if part_type == "text":
        return TextPart(content=data.get("content", ""), parent_task_call_id=parent)
    if part_type == "reasoning":
        return ReasoningPart(content=data.get("content", ""), parent_task_call_id=parent)
    if part_type == "tool":
        return ToolPart(
            name=data.get("name") or "",
            arguments=data.get("input"),
            output=data.get("output"),
            tool_call_id=data.get("tool_call_id"),
            duration_ms=data.get("duration_ms"),
            parent_task_call_id=parent,
            status=data.get("status") or "running",
            error=data.get("error"),
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

    def append_reasoning(self, reasoning: str, parent_task_call_id: Optional[str] = None) -> None:
        self._content.parts.append(
            ReasoningPart(content=reasoning, parent_task_call_id=parent_task_call_id),
        )

    def append_reasoning_delta(
        self,
        reasoning: str,
        parent_task_call_id: Optional[str] = None,
    ) -> None:
        """流式思考增量：合并进最后一个 reasoning part，否则新建。"""
        if not reasoning:
            return
        if (
            self._content.parts
            and isinstance(self._content.parts[-1], ReasoningPart)
            and self._content.parts[-1].parent_task_call_id == parent_task_call_id
        ):
            last = self._content.parts[-1]
            last.content = (last.content or "") + reasoning
        else:
            self._content.parts.append(
                ReasoningPart(content=reasoning, parent_task_call_id=parent_task_call_id),
            )

    def append_tool(
        self,
        tool: str,
        tool_input: Optional[Dict[str, Any]] = None,
        tool_call_id: Optional[str] = None,
        parent_task_call_id: Optional[str] = None,
    ) -> None:
        part = ToolPart(
            name=tool,
            arguments=tool_input,
            tool_call_id=tool_call_id,
            parent_task_call_id=parent_task_call_id,
            status="running",
        )
        self._content.parts.append(part)
        self._last_tool = part
        if tool_call_id:
            self._tools_by_call_id[tool_call_id] = part

    def append_tool_output(
        self,
        tool: str,
        output: str,
        tool_call_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
        *,
        status: str = "success",
        error: Optional[str] = None,
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
        if duration_ms is not None:
            target.duration_ms = duration_ms
        if tool_call_id:
            target.tool_call_id = tool_call_id
        if target is self._last_tool:
            self._last_tool = None

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
