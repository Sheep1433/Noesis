"""Context normalization, compaction and final request snapshot owner."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from noesis.llm.model_limits import resolve_context_max_tokens
from noesis.agents.middlewares.kernel.context_metrics import estimate_model_request_input_tokens
from noesis.runtime.context_snapshot import ContextSnapshot, ContextSource, set_context_snapshot
from noesis.runtime.outcome import RuntimePhase, RuntimeStatus, StopReason, current_runtime_outcome, outcome, set_runtime_outcome
from noesis.runtime.logging import logger


_CLOCK_KEY = "noesis_session_clock"
_CLOCK_TZ = "Asia/Shanghai"
_CONTEXT_EXHAUSTED_TEXT = "当前上下文已达到模型限制，请缩短输入或开始新的会话。"


class ContextLifecycleMiddleware(AgentMiddleware[AgentState]):
    def __init__(self, *, model_id: str | None = None, compaction_engine: Any | None = None, timezone_name: str = _CLOCK_TZ) -> None:
        super().__init__()
        self.model_id = model_id
        self.compaction_engine = compaction_engine
        self._tz = ZoneInfo(timezone_name)

    @staticmethod
    def normalize_messages(messages: list[Any]) -> list[Any]:
        """Make restored AI tool calls provider-safe without changing valid history."""
        existing = {str(getattr(message, "tool_call_id", "")) for message in messages if isinstance(message, ToolMessage)}
        patched: list[Any] = []
        inserted: set[str] = set()
        for message in messages:
            patched.append(message)
            if getattr(message, "type", None) != "ai":
                continue
            calls = list(getattr(message, "tool_calls", None) or [])
            if not calls:
                for raw in (getattr(message, "additional_kwargs", {}) or {}).get("tool_calls", []) or []:
                    if not isinstance(raw, dict):
                        continue
                    function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                    raw_args = raw.get("args") or function.get("arguments") or {}
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            raw_args = {}
                    calls.append({
                        "id": raw.get("id"),
                        "name": raw.get("name") or function.get("name") or "unknown",
                        "args": raw_args if isinstance(raw_args, dict) else {},
                    })
            for call in calls:
                call_id = str(call.get("id") or "")
                if call_id and call_id not in existing and call_id not in inserted:
                    patched.append(ToolMessage(content="[Tool call was interrupted and did not return a result.]", tool_call_id=call_id, name=call.get("name", "unknown"), status="error"))
                    inserted.add(call_id)
        return patched

    def _state_update(self, state: AgentState) -> dict | None:
        messages = list(state.get("messages") or [])
        normalized = self.normalize_messages(messages)
        if normalized == messages:
            return None
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *normalized]}

    def before_model(self, state: AgentState, runtime) -> dict | None:
        update = self._state_update(state)
        if self.compaction_engine is not None:
            try:
                compacted = self.compaction_engine.before_model({**state, "messages": self.normalize_messages(list(state.get("messages") or []))}, runtime)
            except Exception:
                logger.exception("context compaction failed")
                compacted = None
            if compacted:
                return compacted
        return update

    async def abefore_model(self, state: AgentState, runtime) -> dict | None:
        if self.compaction_engine is not None:
            normalized_state = {**state, "messages": self.normalize_messages(list(state.get("messages") or []))}
            try:
                compacted = await self.compaction_engine.abefore_model(normalized_state, runtime)
            except Exception:
                logger.exception("context compaction failed")
                compacted = None
            if compacted:
                return compacted
        return self._state_update(state)

    def _clock_message(self) -> HumanMessage:
        now = datetime.now(self._tz)
        return HumanMessage(content=f"<session_context>\n参考时间：{now:%Y-%m-%d %H:%M:%S}（{now:%A}，{self._tz.key}）\n</session_context>", additional_kwargs={_CLOCK_KEY: True})

    def _final_messages(self, request: ModelRequest) -> list[Any]:
        messages = self.normalize_messages(list(request.messages))
        if any((getattr(message, "additional_kwargs", {}) or {}).get(_CLOCK_KEY) for message in messages):
            return messages
        last_human = next((index for index in range(len(messages) - 1, -1, -1) if getattr(messages[index], "type", None) == "human"), None)
        if last_human is None:
            return messages
        messages.insert(last_human, self._clock_message())
        return messages

    def _snapshot(self, request: ModelRequest, messages: list[Any]) -> ContextSnapshot:
        final_request = request.override(messages=messages)
        current = estimate_model_request_input_tokens(final_request)
        limit = resolve_context_max_tokens(self.model_id)
        state = request.state or {}
        sources = [ContextSource("system_prompt", request.system_prompt or request.system_message, rebuildable=True)]
        if "skills_metadata" in state:
            sources.append(ContextSource("skills", state.get("skills_metadata"), state.get("skills_revision"), rebuildable=True))
        if "memory_contents" in state:
            sources.append(ContextSource("memory", state.get("memory_contents"), rebuildable=True))
        sources.append(ContextSource("session_clock", _CLOCK_KEY, rebuildable=True))
        system_value = request.system_prompt or getattr(request.system_message, "content", None)
        snapshot = ContextSnapshot(tuple(messages), system_value, tuple(sources), tuple(request.tools or ()), current, limit, {"model_id": self.model_id or ""})
        set_context_snapshot(snapshot)
        return snapshot

    def _dispatch(self, request: ModelRequest, handler: Callable):
        active_outcome = current_runtime_outcome()
        if active_outcome is not None and active_outcome.status == RuntimeStatus.STOP and active_outcome.phase == RuntimePhase.GOVERNOR:
            return ModelResponse(result=[AIMessage(content=f"当前运行已停止（{active_outcome.wire_reason}）。", response_metadata={"finish_reason": active_outcome.wire_reason})])
        messages = self._final_messages(request)
        snapshot = self._snapshot(request, messages)
        if snapshot.estimated_tokens > snapshot.model_limit:
            value = outcome(RuntimePhase.CONTEXT, RuntimeStatus.STOP, StopReason.CONTEXT_EXHAUSTED, visible_output_started=False, detail=snapshot.public_dict())
            set_runtime_outcome(value)
            return ModelResponse(result=[AIMessage(content=_CONTEXT_EXHAUSTED_TEXT, response_metadata={"finish_reason": StopReason.CONTEXT_EXHAUSTED.value})])
        return handler(request.override(messages=messages))

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return self._dispatch(request, handler)

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelResponse:
        active_outcome = current_runtime_outcome()
        if active_outcome is not None and active_outcome.status == RuntimeStatus.STOP and active_outcome.phase == RuntimePhase.GOVERNOR:
            return ModelResponse(result=[AIMessage(content=f"当前运行已停止（{active_outcome.wire_reason}）。", response_metadata={"finish_reason": active_outcome.wire_reason})])
        messages = self._final_messages(request)
        snapshot = self._snapshot(request, messages)
        if snapshot.estimated_tokens > snapshot.model_limit:
            value = outcome(RuntimePhase.CONTEXT, RuntimeStatus.STOP, StopReason.CONTEXT_EXHAUSTED, detail=snapshot.public_dict())
            set_runtime_outcome(value)
            return ModelResponse(result=[AIMessage(content=_CONTEXT_EXHAUSTED_TEXT, response_metadata={"finish_reason": StopReason.CONTEXT_EXHAUSTED.value})])
        return await handler(request.override(messages=messages))
