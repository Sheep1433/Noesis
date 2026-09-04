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
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from noesis.runtime.evidence import (
    EvidenceEnvelope,
    RetrievalManifest,
    RetrievalManifestEntry,
)
from noesis.config.env import RetrievalLimitConfig
from noesis.runtime.logging import logger
from noesis.chat.tool_state import (
    ToolState,
    can_transition_tool_state,
    derive_tool_state,
    is_terminal_tool_state,
)


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
    id: str = ""
    content: str = ""
    parent_task_call_id: Optional[str] = None
    type: str = "text"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "content": self.content}
        if self.id:
            out["id"] = self.id
        out.update(_part_parent_fields(self.parent_task_call_id))
        return out


@dataclass
class RetrievalPart(MessagePart):
    id: str = ""
    tool_call_id: str = ""
    query: str = ""
    results: List[Dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    # 来源归属：{"kind": "main"|"subagent", "label": 任务标题}；缺省视为主 Agent
    # 自检索（旧数据无该字段，前端按 main 归组）
    origin: Optional[Dict[str, Any]] = None
    type: str = "retrieval"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "tool_call_id": self.tool_call_id,
            "query": self.query,
            "results": [dict(item) for item in self.results],
        }
        if self.truncated:
            out["truncated"] = True
        if self.origin:
            out["origin"] = dict(self.origin)
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
    step_id: Optional[str] = None
    status: str = "running"
    state: Optional[str] = None
    error: Optional[str] = None
    error_category: Optional[str] = None
    hitl: Optional[Dict[str, Any]] = None
    outcome: Optional[str] = None
    exit_code: Optional[int] = None
    timed_out: Optional[bool] = None
    truncated: Optional[bool] = None
    provider_key: Optional[str] = None
    provider_version: Optional[str] = None
    type: str = "tool"

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = derive_tool_state(
                status=self.status,
                outcome=self.outcome,
                error_category=self.error_category,
                timed_out=self.timed_out,
            ).value

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "name": self.name,
            "input": self.arguments if self.arguments is not None else {},
            "output": self.output,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "state": self.state or ToolState.RUNNING.value,
        }
        if self.duration_ms is not None:
            out["duration_ms"] = self.duration_ms
        if self.error:
            out["error"] = self.error
        if self.error_category:
            out["errorCategory"] = self.error_category
        if self.parent_task_call_id:
            out["parent_task_call_id"] = self.parent_task_call_id
        if self.step_id:
            out["step_id"] = self.step_id
        if self.hitl:
            out["hitl"] = self.hitl
        if self.outcome:
            out["outcome"] = self.outcome
        if self.exit_code is not None:
            out["exit_code"] = self.exit_code
        if self.timed_out is not None:
            out["timed_out"] = self.timed_out
        if self.truncated is not None:
            out["truncated"] = self.truncated
        if self.provider_key:
            out["_provider_key"] = self.provider_key
        if self.provider_version:
            out["_provider_version"] = self.provider_version
        return out

    def to_public_dict(self) -> Dict[str, Any]:
        out = self.to_dict()
        out.pop("_provider_key", None)
        out.pop("_provider_version", None)
        return out


def _part_from_dict(data: Dict[str, Any]) -> MessagePart:
    part_type = data.get("type")
    parent = data.get("parent_task_call_id")
    if part_type == "text":
        return TextPart(
            id=str(data.get("id") or ""),
            content=data.get("content", ""),
            parent_task_call_id=parent,
        )
    if part_type == "reasoning":
        return ReasoningPart(content=data.get("content", ""), parent_task_call_id=parent)
    if part_type == "tool":
        inp = data.get("input")
        if inp is None and "arguments" in data:
            inp = data.get("arguments")
        raw_state = data.get("state")
        try:
            state = ToolState(str(raw_state)).value
        except ValueError:
            state = derive_tool_state(
                status=data.get("status"),
                outcome=data.get("outcome"),
                error_category=data.get("errorCategory"),
                timed_out=data.get("timed_out"),
            ).value
        return ToolPart(
            name=data.get("name") or "",
            arguments=inp if inp is not None else {},
            output=data.get("output"),
            tool_call_id=data.get("tool_call_id"),
            duration_ms=data.get("duration_ms"),
            parent_task_call_id=parent,
            step_id=data.get("step_id"),
            status=data.get("status") or "running",
            state=state,
            error=data.get("error"),
            error_category=data.get("errorCategory"),
            hitl=data.get("hitl"),
            outcome=data.get("outcome"),
            exit_code=data.get("exit_code"),
            timed_out=data.get("timed_out"),
            truncated=data.get("truncated"),
            provider_key=data.get("_provider_key"),
            provider_version=data.get("_provider_version"),
        )
    if part_type == "retrieval":
        results = data.get("results")
        origin = data.get("origin")
        return RetrievalPart(
            id=str(data.get("id") or ""),
            tool_call_id=str(data.get("tool_call_id") or ""),
            query=str(data.get("query") or ""),
            results=[dict(item) for item in results if isinstance(item, dict)]
            if isinstance(results, list)
            else [],
            truncated=bool(data.get("truncated")),
            origin=origin if isinstance(origin, dict) else None,
        )
    raise ValueError(f"Unknown part type: {part_type}")


@dataclass
class MessageContent:
    parts: List[MessagePart] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parts": [p.to_dict() if isinstance(p, MessagePart) else p for p in self.parts]
        }

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "parts": [
                p.to_public_dict()
                if isinstance(p, ToolPart)
                else p.to_dict()
                if isinstance(p, MessagePart)
                else p
                for p in self.parts
            ]
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


def normalize_message_content_for_delivery(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize protocol-owned fields without rejecting unknown future part types."""
    normalized: List[Any] = []
    for raw in data.get("parts", []):
        if isinstance(raw, dict) and raw.get("type") == "tool":
            tool_data = dict(raw)
            hitl = tool_data.get("hitl")
            if not is_terminal_tool_state(tool_data.get("state")) and (
                isinstance(hitl, dict) and hitl.get("status") == "pending"
            ):
                tool_data["state"] = ToolState.APPROVAL_PENDING.value
            normalized.append(_part_from_dict(tool_data).to_public_dict())
        elif isinstance(raw, dict):
            normalized.append(dict(raw))
        else:
            normalized.append(raw)
    return {"parts": normalized}


class AssistantMessageBuilder:
    """累积 text / reasoning / tool parts，并按 tool_call_id 索引以支持并行 / 乱序工具调用。"""

    def __init__(self, session_id: str = "", message_id: str = ""):
        self.session_id = session_id
        self.message_id = message_id
        self._content = MessageContent()
        self._tools_by_call_id: Dict[str, ToolPart] = {}
        self._last_tool: Optional[ToolPart] = None
        self._retrieval_manifest = RetrievalManifest(run_salt=message_id or None)
        self._retrieval_validation_counts: Dict[str, int] = {}

    @staticmethod
    def _new_part_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    def _record_retrieval_rejection(self, reason: str) -> None:
        self._retrieval_validation_counts[reason] = (
            self._retrieval_validation_counts.get(reason, 0) + 1
        )
        logger.warning(
            "retrieval_result_rejected reason={} session_id={} message_id={} count={}",
            reason,
            self.session_id,
            self.message_id,
            self._retrieval_validation_counts[reason],
        )

    @property
    def retrieval_validation_counts(self) -> Dict[str, int]:
        return dict(self._retrieval_validation_counts)

    def append_text(
        self,
        text: str,
        parent_task_call_id: Optional[str] = None,
        *,
        part_id: Optional[str] = None,
    ) -> TextPart:
        part = TextPart(
            id=part_id or "",
            content=text,
            parent_task_call_id=parent_task_call_id,
        )
        self._content.parts.append(
            part,
        )
        return part

    def append_text_delta(
        self,
        text: str,
        parent_task_call_id: Optional[str] = None,
        *,
        part_id: Optional[str] = None,
    ) -> TextPart:
        """流式正文增量：合并进同 parent 最近 text part（跳过其它 parent 交错）。"""
        if not text:
            return
        for part in reversed(self._content.parts):
            if getattr(part, "parent_task_call_id", None) != parent_task_call_id:
                continue
            if isinstance(part, TextPart):
                part.content = (part.content or "") + text
                if part_id and not part.id:
                    part.id = part_id
                return part
            break
        return self.append_text(
            text,
            parent_task_call_id=parent_task_call_id,
            part_id=part_id,
        )

    def register_retrieval_results(
        self,
        *,
        tool_call_id: str,
        query: str,
        results: List[Dict[str, Any]],
        truncated: bool = False,
        origin: Optional[Dict[str, Any]] = None,
        max_results: Optional[int] = None,
    ) -> RetrievalPart:
        """登记 retrieval tool evidence，并持久化独立 retrieval part。

        ``max_results`` 覆盖单次登记条数上限（缺省 RetrievalLimitConfig.
        max_results_per_call）：跨边界来源清单是子会话多轮检索的去重汇总，
        按更高上界登记（见 event_mapping/retrieval.py），不受单工具调用
        上限约束——否则面板「共检索 N」被截成调用级上限。
        """
        per_call_limit = max_results if max_results is not None else RetrievalLimitConfig.max_results_per_call
        registered: List[Dict[str, Any]] = []
        capacity_truncated = len(results) > per_call_limit
        for raw in results[:per_call_limit]:
            # 可引用性准入由 EvidenceEnvelope 身份校验独立判定（KB 需 collection/
            # document/version/segment 四元身份，web 需 url），缺身份条目在下方
            # 校验处拒收并计入 invalid_evidence_envelope
            if not isinstance(raw, dict):
                continue
            try:
                excerpt, excerpt_truncated = self._truncate_utf8(
                    str(raw.get("excerpt") or ""),
                    max_chars=RetrievalLimitConfig.max_excerpt_chars,
                    max_bytes=RetrievalLimitConfig.max_excerpt_bytes,
                )
                locator = raw.get("locator")
                if locator is not None and len(json.dumps(locator, ensure_ascii=False).encode("utf-8")) > RetrievalLimitConfig.max_locator_bytes:
                    locator = None
                    capacity_truncated = True
                capacity_truncated = capacity_truncated or excerpt_truncated
                envelope = EvidenceEnvelope.model_validate({
                    "source_type": raw.get("source_type") or "knowledge_base",
                    "collection_name": raw.get("collection_name"),
                    "document_id": raw.get("document_id"),
                    "document_version_id": raw.get("document_version_id"),
                    "segment_id": raw.get("segment_id"),
                    "url": raw.get("url"),
                    "title": raw.get("title") or raw.get("file_name"),
                    "excerpt": excerpt,
                    "locator": locator,
                    "score": raw.get("score"),
                    "recall_score": raw.get("recall_score"),
                    "rerank_score": raw.get("rerank_score"),
                    "search_mode": raw.get("search_mode"),
                })
            except (TypeError, ValueError):
                self._record_retrieval_rejection("invalid_evidence_envelope")
                continue
            if (
                self._retrieval_manifest.get_by_envelope(envelope) is None
                and len(self._retrieval_manifest.entries()) >= RetrievalLimitConfig.max_results_per_run
            ):
                capacity_truncated = True
                continue
            entry = self._retrieval_manifest.register(envelope, tool_call_id=tool_call_id)
            registered.append(entry.model_dump(mode="json"))
        existing_part = next(
            (
                part
                for part in self._content.parts
                if isinstance(part, RetrievalPart) and part.tool_call_id == tool_call_id
            ),
            None,
        )
        if existing_part is not None:
            by_id = {
                str(item.get("evidence_id")): item
                for item in existing_part.results
                if item.get("evidence_id")
            }
            for item in registered:
                by_id.setdefault(str(item["evidence_id"]), item)
            existing_part.results = list(by_id.values())
            existing_part.query = existing_part.query or query
            existing_part.truncated = existing_part.truncated or truncated or capacity_truncated
            if origin:
                existing_part.origin = dict(origin)
            return existing_part

        part = RetrievalPart(
            id=self._new_part_id("retrieval"),
            tool_call_id=tool_call_id,
            query=query,
            results=registered,
            truncated=truncated or capacity_truncated,
            origin=dict(origin) if origin else None,
        )
        self._content.parts.append(part)
        return part

    @staticmethod
    def _truncate_utf8(value: str, *, max_chars: int, max_bytes: int) -> tuple[str, bool]:
        shortened = value[:max_chars]
        raw = shortened.encode("utf-8")
        if len(raw) <= max_bytes:
            return shortened, shortened != value
        clipped = raw[:max_bytes]
        while clipped:
            try:
                return clipped.decode("utf-8"), True
            except UnicodeDecodeError:
                clipped = clipped[:-1]
        return "", True

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
            if getattr(part, "parent_task_call_id", None) != parent_task_call_id:
                continue
            if isinstance(part, ReasoningPart):
                part.content = (part.content or "") + reasoning
                return
            break
        self._content.parts.append(
            ReasoningPart(content=reasoning, parent_task_call_id=parent_task_call_id),
        )

    def rollback_trailing_stream_parts(self) -> int:
        """丢弃末尾连续的 text/reasoning parts，返回丢弃数量。

        用于 LLM 重试/降级：失败尝试在被断流前已流出的部分正文与思考
        不应留在消息里（否则 N 次重试累积 N 份重复）。工具/检索等 part
        是模型调用边界——模型流式阶段不产生它们，遇到即停。
        """
        dropped = 0
        while self._content.parts:
            last = self._content.parts[-1]
            if isinstance(last, (TextPart, ReasoningPart)):
                self._content.parts.pop()
                dropped += 1
                continue
            break
        return dropped

    def append_tool(
        self,
        tool: str,
        tool_input: Optional[Dict[str, Any]] = None,
        tool_call_id: Optional[str] = None,
        parent_task_call_id: Optional[str] = None,
        *,
        status: str = "running",
        state: ToolState | str = ToolState.RUNNING,
        hitl: Optional[Dict[str, Any]] = None,
        step_id: Optional[str] = None,
        provider_key: Optional[str] = None,
        provider_version: Optional[str] = None,
    ) -> None:
        if tool_call_id:
            existing = self._tools_by_call_id.get(tool_call_id)
            if existing is not None:
                existing.name = tool or existing.name
                existing.arguments = tool_input if tool_input is not None else existing.arguments
                if parent_task_call_id:
                    existing.parent_task_call_id = parent_task_call_id
                if step_id:
                    existing.step_id = step_id
                if provider_key:
                    existing.provider_key = provider_key
                if provider_version:
                    existing.provider_version = provider_version
                if hitl:
                    existing.hitl = {**(existing.hitl or {}), **hitl}
                # 重放 tool start 只补充同一块的输入信息，不能把已有结果退回 running。
                if not is_terminal_tool_state(existing.state):
                    existing.status = status
                    if can_transition_tool_state(existing.state, state):
                        existing.state = ToolState(str(state)).value
                    self._last_tool = existing
                return
        part = ToolPart(
            name=tool,
            arguments=tool_input,
            tool_call_id=tool_call_id,
            parent_task_call_id=parent_task_call_id,
            step_id=step_id,
            status=status,
            state=ToolState(str(state)).value,
            hitl=hitl,
            provider_key=provider_key,
            provider_version=provider_version,
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
            and part.state in {ToolState.RUNNING, ToolState.APPROVAL_PENDING}
            and isinstance(part.hitl, dict)
            and part.hitl.get("status") in {"pending", "approved", "answered"}
        ]
        return candidates[0] if len(candidates) == 1 else None

    def get_tool(self, tool_call_id: str) -> Optional[ToolPart]:
        return self._tools_by_call_id.get(tool_call_id)

    def has_failed_child_tool(self, parent_tool_call_id: str) -> bool:
        return any(
            isinstance(part, ToolPart)
            and part.parent_task_call_id == parent_tool_call_id
            and part.state in {ToolState.FAILED, ToolState.TIMED_OUT}
            for part in self._content.parts
        )

    def load_from_content_dict(self, data: Dict[str, Any]) -> None:
        """从已落库 content 恢复 parts（HITL resume 续写同一 assistant 行）。"""
        self._content = MessageContent.from_dict(data or {"parts": []})
        self._tools_by_call_id = {}
        self._last_tool = None
        self._retrieval_validation_counts = {}
        self._retrieval_manifest = RetrievalManifest(run_salt=self.message_id or None)
        for part in self._content.parts:
            if not isinstance(part, RetrievalPart):
                continue
            for raw in part.results:
                try:
                    self._retrieval_manifest.ingest(
                        RetrievalManifestEntry.model_validate(raw)
                    )
                except (TypeError, ValueError):
                    continue
        canonical_parts: List[MessagePart] = []
        for part in self._content.parts:
            if isinstance(part, ToolPart) and part.tool_call_id:
                existing = self._tools_by_call_id.get(part.tool_call_id)
                if existing is not None:
                    existing.name = part.name or existing.name
                    existing.arguments = part.arguments or existing.arguments
                    existing.output = part.output if part.output is not None else existing.output
                    if can_transition_tool_state(existing.state, part.state):
                        existing.status = part.status or existing.status
                        existing.state = part.state
                    existing.error = part.error or existing.error
                    existing.error_category = part.error_category or existing.error_category
                    existing.duration_ms = part.duration_ms or existing.duration_ms
                    existing.parent_task_call_id = (
                        part.parent_task_call_id or existing.parent_task_call_id
                    )
                    existing.hitl = {**(existing.hitl or {}), **(part.hitl or {})} or None
                    existing.outcome = part.outcome or existing.outcome
                    existing.exit_code = part.exit_code if part.exit_code is not None else existing.exit_code
                    existing.timed_out = part.timed_out if part.timed_out is not None else existing.timed_out
                    existing.truncated = part.truncated if part.truncated is not None else existing.truncated
                    continue
                self._tools_by_call_id[part.tool_call_id] = part
                if not is_terminal_tool_state(part.state):
                    self._last_tool = part
            canonical_parts.append(part)
        self._content.parts = canonical_parts

    def update_tool_hitl(
        self,
        tool_call_id: Optional[str],
        hitl: Dict[str, Any],
        *,
        status: Optional[str] = None,
        state: ToolState | str | None = None,
    ) -> None:
        if not tool_call_id:
            return
        target = self._tools_by_call_id.get(tool_call_id)
        if target is None:
            return
        target.hitl = {**(target.hitl or {}), **hitl}
        if state is None:
            if status is not None and not is_terminal_tool_state(target.state):
                target.status = status
            return
        if can_transition_tool_state(target.state, state):
            if status is not None:
                target.status = status
            target.state = ToolState(str(state)).value

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
        state: ToolState | str | None = None,
        outcome: Optional[str] = None,
        exit_code: Optional[int] = None,
        timed_out: Optional[bool] = None,
        truncated: Optional[bool] = None,
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

        desired_state = ToolState(str(state)) if state is not None else derive_tool_state(
            status=status,
            outcome=outcome,
            error_category=error_category,
            timed_out=timed_out,
        )
        if is_terminal_tool_state(target.state) and target.state != desired_state:
            return
        if not can_transition_tool_state(target.state, desired_state):
            return
        target.output = output
        target.status = status
        target.state = desired_state.value
        target.error = error
        target.error_category = error_category
        target.outcome = outcome
        target.exit_code = exit_code
        target.timed_out = timed_out
        target.truncated = truncated
        if duration_ms is not None:
            target.duration_ms = duration_ms
        if tool_call_id:
            target.tool_call_id = tool_call_id
        if target is self._last_tool:
            self._last_tool = None

    def reconcile_nonterminal_tools(
        self,
        state: ToolState,
        message: str = "",
        *,
        keep_approval_call_ids: set[str] | None = None,
    ) -> int:
        keep = keep_approval_call_ids or set()
        approval_parents = {
            part.parent_task_call_id
            for part in self._content.parts
            if isinstance(part, ToolPart)
            and part.tool_call_id in keep
            and part.parent_task_call_id
        }
        count = 0
        for part in self._content.parts:
            if not isinstance(part, ToolPart) or is_terminal_tool_state(part.state):
                continue
            if part.tool_call_id in keep or part.tool_call_id in approval_parents:
                part.state = ToolState.APPROVAL_PENDING.value
                continue
            part.state = state.value
            part.status = "error" if state != ToolState.SUCCEEDED else "success"
            part.outcome = {
                ToolState.CANCELLED: "cancelled",
                ToolState.REJECTED: "rejected",
                ToolState.TIMED_OUT: "timed_out",
            }.get(state, part.outcome or "unknown")
            if message:
                part.error = message
                if part.output is None:
                    part.output = message
            count += 1
        self._last_tool = None
        return count

    def mark_running_tools_unknown(
        self,
        message: str,
        *,
        error_category: str = "unknown",
    ) -> int:
        """停止/重启时不推断远程副作用，收口所有未完成工具。"""
        count = 0
        for part in self._content.parts:
            if not isinstance(part, ToolPart) or is_terminal_tool_state(part.state):
                continue
            part.status = "error"
            part.state = ToolState.FAILED.value
            part.outcome = "unknown"
            part.error = message
            part.error_category = error_category
            if part.output is None:
                part.output = message
            count += 1
        self._last_tool = None
        return count

    def to_dict(self) -> Dict[str, Any]:
        return self._content.to_dict()

    def to_public_dict(self) -> Dict[str, Any]:
        return self._content.to_public_dict()

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

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
