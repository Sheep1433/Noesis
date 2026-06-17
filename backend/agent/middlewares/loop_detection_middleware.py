"""检测并打断重复工具调用循环。

在 after_model 中统计「工具名 + 参数」哈希；达到阈值时于下次 wrap_model_call 注入 HumanMessage 警告
（避免破坏 tool_calls / ToolMessage 配对）。超过硬上限则剥离 tool_calls，强制文本收尾。
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from typing_extensions import override

from config.env import ModelConfig
from common.logging import logger

_SALIENT_ARG_FIELDS = ("path", "url", "query", "command", "pattern", "glob", "cmd")

_WARNING_MSG = (
    "[检测到循环] 你正在重复相同的工具调用。请停止调用工具并立即给出最终答案。"
    "若无法完成任务，请总结目前已完成的工作。"
)

_HARD_STOP_MSG = (
    "[强制停止] 重复的工具调用已超过安全上限。将基于目前已收集的结果产出最终答案。"
)


def _normalize_tool_call_args(raw_args: object) -> tuple[dict, str | None]:
    if isinstance(raw_args, dict):
        return raw_args, None
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}, raw_args
        if isinstance(parsed, dict):
            return parsed, None
        return {}, json.dumps(parsed, sort_keys=True, default=str)
    if raw_args is None:
        return {}, None
    return {}, json.dumps(raw_args, sort_keys=True, default=str)


def _stable_tool_key(name: str, args: dict, fallback_key: str | None) -> str:
    stable_args = {
        field: args[field]
        for field in _SALIENT_ARG_FIELDS
        if args.get(field) is not None
    }
    if stable_args:
        return json.dumps(stable_args, sort_keys=True, default=str)
    if fallback_key is not None:
        return fallback_key
    return json.dumps(args, sort_keys=True, default=str)


def _hash_tool_calls(tool_calls: list[dict]) -> str:
    normalized: list[str] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args, fallback_key = _normalize_tool_call_args(tc.get("args", {}))
        key = _stable_tool_key(name, args, fallback_key)
        normalized.append(f"{name}:{key}")
    normalized.sort()
    blob = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


class LoopDetectionMiddleware(AgentMiddleware[AgentState]):
    """检测相同工具调用集合的重复，警告后强制文本收尾。"""

    def __init__(
        self,
        warn_threshold: int | None = None,
        hard_limit: int | None = None,
        *,
        window_size: int | None = None,
        max_tracked_threads: int | None = None,
    ):
        super().__init__()
        self.warn_threshold = warn_threshold or ModelConfig.loop_detection_warn_threshold
        self.hard_limit = hard_limit or ModelConfig.loop_detection_hard_limit
        self._window_size = window_size or ModelConfig.loop_detection_window_size
        self._max_tracked_threads = (
            max_tracked_threads or ModelConfig.loop_detection_max_tracked_threads
        )
        if self.warn_threshold >= self.hard_limit:
            raise ValueError("warn_threshold 必须小于 hard_limit")
        self._lock = threading.Lock()
        self._history: OrderedDict[str, list[str]] = OrderedDict()
        self._warned: dict[str, set[str]] = defaultdict(set)
        self._pending_warnings: dict[str, list[str]] = defaultdict(list)

    def _get_thread_id(self, runtime: Runtime) -> str:
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        return str(thread_id) if thread_id else "default"

    def _evict_if_needed(self) -> None:
        while len(self._history) > self._max_tracked_threads:
            evicted_id, _ = self._history.popitem(last=False)
            self._warned.pop(evicted_id, None)
            self._pending_warnings.pop(evicted_id, None)

    def _queue_pending_warning(self, runtime: Runtime, warning: str) -> None:
        thread_id = self._get_thread_id(runtime)
        with self._lock:
            warnings = self._pending_warnings[thread_id]
            if warning not in warnings:
                warnings.append(warning)
            pending_count = len(warnings)
        logger.info(
            "[loop_detection] 警告已入队，等待下次模型调用注入 | thread_id={} pending_count={}",
            thread_id,
            pending_count,
        )

    def _track_and_check(
        self, state: AgentState, runtime: Runtime
    ) -> tuple[str | None, bool]:
        messages = state.get("messages", [])
        if not messages:
            return None, False

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None, False

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None, False

        thread_id = self._get_thread_id(runtime)
        call_hash = _hash_tool_calls(tool_calls)

        with self._lock:
            if thread_id in self._history:
                self._history.move_to_end(thread_id)
            else:
                self._history[thread_id] = []
                self._evict_if_needed()

            history = self._history[thread_id]
            history.append(call_hash)
            if len(history) > self._window_size:
                history[:] = history[-self._window_size :]

            warned_hashes = self._warned.get(thread_id)
            if warned_hashes is not None:
                warned_hashes.intersection_update(history)
                if not warned_hashes:
                    self._warned.pop(thread_id, None)

            count = history.count(call_hash)
            tool_names = [tc.get("name", "?") for tc in tool_calls]

        if count >= self.hard_limit:
            logger.error(
                "[loop_detection] 硬停止 | thread_id={} hash={} count={}/{} tools={}",
                thread_id,
                call_hash,
                count,
                self.hard_limit,
                tool_names,
            )
            return _HARD_STOP_MSG, True

        if count >= self.warn_threshold:
            with self._lock:
                warned = self._warned[thread_id]
                already_warned = call_hash in warned
                if not already_warned:
                    warned.add(call_hash)
            if already_warned:
                return None, False
            logger.warning(
                "[loop_detection] 触发警告 | thread_id={} hash={} count={}/{} tools={}",
                thread_id,
                call_hash,
                count,
                self.warn_threshold,
                tool_names,
            )
            return _WARNING_MSG, False

        if count == self.warn_threshold - 1:
            logger.info(
                "[loop_detection] 距警告还差 1 次 | thread_id={} hash={} count={} tools={}",
                thread_id,
                call_hash,
                count,
                tool_names,
            )

        return None, False

    @staticmethod
    def _append_text(content: str | list | None, text: str) -> str | list:
        if content is None:
            return text
        if isinstance(content, list):
            return [*content, {"type": "text", "text": f"\n\n{text}"}]
        if isinstance(content, str):
            return content + f"\n\n{text}"
        return str(content) + f"\n\n{text}"

    @staticmethod
    def _build_hard_stop_update(last_msg, content: str | list) -> dict:
        update = {"tool_calls": [], "content": content}
        additional_kwargs = dict(getattr(last_msg, "additional_kwargs", {}) or {})
        for key in ("tool_calls", "function_call"):
            additional_kwargs.pop(key, None)
        update["additional_kwargs"] = additional_kwargs

        response_metadata = deepcopy(getattr(last_msg, "response_metadata", {}) or {})
        if response_metadata.get("finish_reason") == "tool_calls":
            response_metadata["finish_reason"] = "stop"
        update["response_metadata"] = response_metadata
        return update

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        warning, hard_stop = self._track_and_check(state, runtime)

        if hard_stop:
            messages = state.get("messages", [])
            last_msg = messages[-1]
            content = self._append_text(last_msg.content, warning or _HARD_STOP_MSG)
            stripped_msg = last_msg.model_copy(
                update=self._build_hard_stop_update(last_msg, content)
            )
            return {"messages": [stripped_msg]}

        if warning:
            self._queue_pending_warning(runtime, warning)
        return None

    def _clear_pending_warnings(self, runtime: Runtime, *, reason: str) -> None:
        thread_id = self._get_thread_id(runtime)
        with self._lock:
            dropped = self._pending_warnings.pop(thread_id, None)
        if dropped:
            logger.info(
                "[loop_detection] 清空排队警告 | thread_id={} reason={} dropped_count={}",
                thread_id,
                reason,
                len(dropped),
            )

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_pending_warnings(runtime, reason="before_agent")
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self.before_agent(state, runtime)
        return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.after_model(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_pending_warnings(runtime, reason="after_agent")
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.after_agent(state, runtime)

    def _drain_pending_warnings(self, runtime: Runtime) -> list[str]:
        thread_id = self._get_thread_id(runtime)
        with self._lock:
            return self._pending_warnings.pop(thread_id, [])

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        thread_id = self._get_thread_id(request.runtime)
        warnings = self._drain_pending_warnings(request.runtime)
        if not warnings:
            return request
        deduped = list(dict.fromkeys(warnings))
        logger.info(
            "[loop_detection] 注入警告到模型请求 | thread_id={} warning_count={} msg_count={}",
            thread_id,
            len(deduped),
            len(request.messages),
        )
        new_messages = [
            *request.messages,
            HumanMessage(content="\n\n".join(deduped), name="loop_warning"),
        ]
        return request.override(messages=new_messages)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))

    def reset(self, thread_id: str | None = None) -> None:
        with self._lock:
            if thread_id:
                self._history.pop(thread_id, None)
                self._warned.pop(thread_id, None)
                self._pending_warnings.pop(thread_id, None)
            else:
                self._history.clear()
                self._warned.clear()
                self._pending_warnings.clear()
        logger.info("[loop_detection] reset | thread_id={}", thread_id or "ALL")
