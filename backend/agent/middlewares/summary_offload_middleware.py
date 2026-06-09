"""Summarization + tool-result offload middleware (`before_model`).

When context usage reaches the configured fraction (default 85% of max input):
1. Offload oversized ToolMessage bodies to filesystem when available, else discard.
2. If still above retention ratio, run LLM summarization via LangChain base class.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from langchain.agents.middleware.summarization import (
    SummarizationMiddleware as LCSummarizationMiddleware,
    TokenCounter,
)
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import (
    AnyMessage,
    MessageLikeRepresentation,
    RemoveMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime
from typing_extensions import override

from config.env import ModelConfig
from llm import get_llm
from utils.log_util import logger

_OFFLOAD_DIR = "summary_offload"
_OFFLOAD_PATH_PREFIX = f"/{_OFFLOAD_DIR}"

_DISCARD_PLACEHOLDER = (
    "[ToolResultOffloaded]\n\n"
    "工具输出因上下文接近上限已丢弃以释放 token。"
    "如需完整结果，请缩小查询范围或重新执行该工具。"
)

_OFFLOAD_PLACEHOLDER_TEMPLATE = (
    "[ToolResultOffloaded]\n\n"
    "文件路径: {file_path}\n"
    "可使用 read_file 工具读取完整内容\n\n"
    "--- 内容预览 ---\n{content_sample}"
)


def _get_content_str(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if len(content) == 1 and isinstance(content[0], dict) and content[0].get("type") == "text":
            return str(content[0].get("text", ""))
        return None
    return None


def _build_offload_file_path(msg: ToolMessage) -> str:
    tool_name = msg.name or "unknown"
    message_id = msg.id or str(uuid.uuid4())[:8]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
    return f"{_OFFLOAD_PATH_PREFIX}/{safe_name}-{message_id}.txt"


def _format_offload_placeholder(file_path: str, content_sample: str) -> str:
    return _OFFLOAD_PLACEHOLDER_TEMPLATE.format(
        file_path=file_path,
        content_sample=content_sample,
    )


def _resolve_filesystem_backend(runtime: Runtime, state: AgentState) -> Any | None:
    """Resolve filesystem BackendProtocol when the agent graph has filesystem support."""
    context = getattr(runtime, "context", None)
    if context is not None:
        backend = getattr(context, "backend", None)
        if backend is None and isinstance(context, dict):
            backend = context.get("backend")
        if callable(backend):
            try:
                backend = backend(runtime)
            except TypeError:
                try:
                    backend = backend()
                except TypeError:
                    backend = None
        if backend is not None:
            return backend

    if "files" in state:
        from deepagents.backends import StateBackend

        return StateBackend()

    return None


def _write_offloaded_content(backend: Any, file_path: str, content: str) -> tuple[bool, dict[str, Any]]:
    result = backend.write(file_path, content)
    if getattr(result, "error", None):
        return False, {}
    return True, getattr(result, "files_update", None) or {}


def _tool_header(msg: ToolMessage, content_str: str) -> str:
    tool_name = msg.name or "unknown"
    tool_call_id = msg.tool_call_id or ""
    header_lines = [
        "=== Tool Invocation ===",
        f"Tool: {tool_name}",
        f"Tool Call ID: {tool_call_id}",
        "=" * 40,
        "",
    ]
    return "\n".join(header_lines) + content_str


def _preview_sample(content_str: str) -> str:
    preview_lines = content_str.splitlines()[:10]
    return "\n".join(line[:500] for line in preview_lines)


def _process_tool_message(
    msg: ToolMessage,
    *,
    threshold: int,
    token_counter: TokenCounter,
    backend: Any | None,
) -> dict[str, Any] | None:
    content_str = _get_content_str(msg.content)
    if content_str is None:
        return None

    if token_counter([msg]) <= threshold:
        return None

    if backend is not None:
        file_path = _build_offload_file_path(msg)
        written, files_update = _write_offloaded_content(
            backend, file_path, _tool_header(msg, content_str)
        )
        if written:
            msg.content = _format_offload_placeholder(file_path, _preview_sample(content_str))
            return files_update
        logger.warning(
            "[summary_offload] 写入文件系统失败，改为丢弃 tool 输出 | path={}",
            file_path,
        )

    msg.content = _DISCARD_PLACEHOLDER
    return {}


def _offload_tool_results(
    messages: list[AnyMessage],
    threshold: int,
    token_counter: TokenCounter,
    runtime: Runtime,
    state: AgentState,
) -> tuple[dict[str, Any], list[AnyMessage]]:
    backend = _resolve_filesystem_backend(runtime, state)
    files_update: dict[str, Any] = {}
    modified_messages: list[AnyMessage] = []

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        result = _process_tool_message(
            msg,
            threshold=threshold,
            token_counter=token_counter,
            backend=backend,
        )
        if result is not None:
            files_update.update(result)
            modified_messages.append(msg)

    return files_update, modified_messages


class SummarizationOffloadMiddleware(LCSummarizationMiddleware):
    """Token-fraction trigger: offload tool outputs first, then summarize if needed."""

    def __init__(
        self,
        model,
        *,
        tool_offload_threshold: int | None = None,
        max_retention_ratio: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model, **kwargs)
        self.tool_offload_threshold = (
            tool_offload_threshold
            if tool_offload_threshold is not None
            else ModelConfig.summarization_tool_offload_threshold
        )
        self.max_retention_ratio = (
            max_retention_ratio
            if max_retention_ratio is not None
            else ModelConfig.summarization_max_retention_ratio
        )

    @override
    def _get_profile_limits(self) -> int | None:
        configured = ModelConfig.summarization_max_input_tokens
        if configured > 0:
            return configured
        return super()._get_profile_limits()

    def _get_token_trigger_value(self) -> int | None:
        if not self._trigger_conditions:
            return None
        for kind, value in self._trigger_conditions:
            if kind == "tokens":
                return int(value)
            if kind == "fraction":
                max_input_tokens = self._get_profile_limits()
                if max_input_tokens:
                    return int(max_input_tokens * value)
        return None

    @staticmethod
    def _find_cutoff_by_token_limit(messages: list[AnyMessage], max_tokens: int, token_counter: TokenCounter) -> int:
        if not messages or token_counter(messages) <= max_tokens:
            return 0

        left, right = 0, len(messages)
        cutoff_candidate = len(messages)
        max_iterations = len(messages).bit_length() + 1

        for _ in range(max_iterations):
            if left >= right:
                break
            mid = (left + right) // 2
            if token_counter(messages[mid:]) <= max_tokens:
                cutoff_candidate = mid
                right = mid
            else:
                left = mid + 1

        if cutoff_candidate == len(messages):
            cutoff_candidate = left

        return LCSummarizationMiddleware._find_safe_cutoff_point(messages, cutoff_candidate)

    def _run_before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None

        files_update, modified_messages = _offload_tool_results(
            messages,
            self.tool_offload_threshold,
            self.token_counter,
            runtime,
            state,
        )

        current_tokens = self.token_counter(messages)
        trigger_value = self._get_token_trigger_value()

        if trigger_value is not None:
            retention_limit = int(trigger_value * self.max_retention_ratio)
            if current_tokens <= retention_limit:
                if not files_update and not modified_messages:
                    return None
                result: dict[str, Any] = {}
                if modified_messages:
                    result["messages"] = modified_messages
                if files_update:
                    result["files"] = files_update
                return result or None

            system_msg_count = 0
            messages_to_process = messages
            if messages and messages[0].type == "system":
                system_msg_count = 1
                messages_to_process = messages[1:]

            cutoff_relative = self._find_cutoff_by_token_limit(
                messages_to_process, retention_limit, self.token_counter
            )
            cutoff_index = system_msg_count + cutoff_relative
        else:
            cutoff_index = self._determine_cutoff_index(messages)

        if cutoff_index <= 0:
            if not files_update and not modified_messages:
                return None
            result = {}
            if modified_messages:
                result["messages"] = modified_messages
            if files_update:
                result["files"] = files_update
            return result

        system_message = messages[0] if messages and messages[0].type == "system" else None
        conversation_messages = messages[1:] if system_message is not None else messages

        messages_to_summarize, preserved_messages = self._partition_messages(
            conversation_messages,
            cutoff_index - (1 if system_message is not None else 0),
        )
        summary = self._create_summary(messages_to_summarize)
        new_messages = self._build_new_messages(summary)

        final_messages: list[AnyMessage] = []
        if system_message is not None:
            final_messages.append(system_message)
        final_messages.extend(new_messages)
        final_messages.extend(preserved_messages)

        result = {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *final_messages]}
        if files_update:
            result["files"] = files_update

        logger.info(
            "[summary_offload] 已摘要历史 | tokens_before={} tokens_after≈{}",
            current_tokens,
            self.token_counter(final_messages),
        )
        return result

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._run_before_model(state, runtime)

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._run_before_model, state, runtime)


def _default_max_input_tokens() -> int:
    if ModelConfig.summarization_max_input_tokens > 0:
        return ModelConfig.summarization_max_input_tokens
    return max(1, int(ModelConfig.max_tokens))


def create_summary_offload_middleware() -> SummarizationOffloadMiddleware | None:
    if not ModelConfig.summarization_enabled:
        return None

    model = get_llm(purpose="summarization")
    max_input = ModelConfig.summarization_max_input_tokens or _default_max_input_tokens()

    if not getattr(model, "profile", None):
        model.profile = {"max_input_tokens": max_input}

    fraction = ModelConfig.summarization_trigger_fraction
    return SummarizationOffloadMiddleware(
        model,
        trigger=("fraction", fraction),
        keep=("messages", ModelConfig.summarization_messages_to_keep),
        tool_offload_threshold=ModelConfig.summarization_tool_offload_threshold,
        max_retention_ratio=ModelConfig.summarization_max_retention_ratio,
    )
