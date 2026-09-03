"""Inject run-stable dynamic context with a prefix-cache-safe layout.

- 会话首轮把 Runtime Context 冻结成头部块（messages[0]，system prompt 与
  首条 user 消息之间），存入 private state、之后逐字节不变——冻结内容
  只到日期粒度，一天之内所有轮次完全相同；
- 日期粒度足够（模型需要的是「今天几号」，不是「现在几点」），分钟/
  小时级时间戳会让每轮头部内容必然变化；
- 跨日不改写冻结块：过期日期故意保留，新一轮在消息尾部追加纠正声明
  ——改写头部等于整段前缀缓存作废（Claude Code 实测一次跨夜重写代价
  ~920K effective tokens）；
- 附件清单是逐轮变化信息，不属于冻结块：本轮附件集合变化时在尾部
  追加声明（尾部追加只花自己的 token，不动前缀）。

曾经的「每轮插在最后一条 user 消息前、不进历史」方案（insert_late_context）
会让公共前缀在注入位置断开——上一轮全部新增在新一轮再 miss 一次，
已退役。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ResponseT,
)
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from noesis.runtime.logging import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable


HEAD_TEMPLATE = """## Runtime Context

Today's date is {date} ({timezone}).
{workspace_line}"""

_HEAD_MARKER = "noesis_runtime_context_head"
_TAIL_MARKER = "noesis_runtime_context_tail"


@dataclass(frozen=True)
class DynamicContextBlock:
    """Resolved dynamic inputs for one agent run."""

    date: str
    timezone: str
    workspace: str | None = None
    attachments: tuple[str, ...] = field(default_factory=tuple)


class DynamicContextState(AgentState):
    """Private state: frozen head block + per-run tail corrections.

    ``dynamic_context_block``/``dynamic_context_date`` 首轮冻结、之后不变；
    ``dynamic_context_attachments`` 记录最近一次见到的非空附件集合；
    ``dynamic_context_tail`` 每 run 边界重算（跨日纠正 + 新附件声明）。
    """

    dynamic_context_block: NotRequired[Annotated[str, PrivateStateAttr]]
    dynamic_context_date: NotRequired[Annotated[str, PrivateStateAttr]]
    dynamic_context_attachments: NotRequired[Annotated[tuple[str, ...], PrivateStateAttr]]
    dynamic_context_tail: NotRequired[Annotated[str, PrivateStateAttr]]


# State keys this middleware owns; subagent isolation must carry these over
# (子 Agent 继承父会话的冻结块与日期，同会话语义一致)。
PRIVATE_STATE_KEYS: tuple[str, ...] = (
    "dynamic_context_block",
    "dynamic_context_date",
    "dynamic_context_attachments",
    "dynamic_context_tail",
)


DynamicContextProvider = Callable[[], "DynamicContextBlock | Awaitable[DynamicContextBlock]"]


def render_head_block(
    block: DynamicContextBlock,
    *,
    template: str = HEAD_TEMPLATE,
) -> str:
    """Render the frozen head block in a deterministic layout."""
    workspace_line = f"Workspace: {block.workspace}" if block.workspace else ""
    return template.format(
        date=block.date,
        timezone=block.timezone,
        workspace_line=workspace_line,
    ).rstrip()


class DynamicContextMiddleware(
    AgentMiddleware[DynamicContextState, ContextT, ResponseT],
):
    """Freeze dynamic context once per conversation; correct at the tail."""

    state_schema = DynamicContextState

    def __init__(
        self,
        context_provider: DynamicContextProvider | None = None,
        *,
        template: str = HEAD_TEMPLATE,
    ) -> None:
        self._context_provider = context_provider
        self._template = template

    # ---------- run boundary ----------

    def _resolve_update(self, state: DynamicContextState, block: DynamicContextBlock) -> dict[str, object]:
        frozen = str(state.get("dynamic_context_block") or "")
        frozen_date = str(state.get("dynamic_context_date") or "")
        stored_attachments = tuple(state.get("dynamic_context_attachments") or ())
        updates: dict[str, object] = {}

        if not frozen:
            updates["dynamic_context_block"] = render_head_block(block, template=self._template)
            updates["dynamic_context_date"] = block.date
            frozen_date = block.date

        tail_parts: list[str] = []
        if block.date != frozen_date:
            tail_parts.append(f"Today's date is now {block.date} ({block.timezone}).")
        current_attachments = tuple(block.attachments or ())
        if current_attachments and current_attachments != stored_attachments:
            tail_parts.append("Attachments available: " + ", ".join(current_attachments) + ".")
            updates["dynamic_context_attachments"] = current_attachments

        updates["dynamic_context_tail"] = "\n".join(tail_parts)
        return updates

    def before_agent(
        self,
        state: DynamicContextState,
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, object] | None:
        """Resolve the block at the LangChain run boundary."""
        if self._context_provider is None:
            return {"dynamic_context_block": "", "dynamic_context_tail": ""}
        block = self._context_provider()
        if inspect.isawaitable(block):
            close = getattr(block, "close", None)
            if callable(close):
                close()
            raise TypeError(
                "DynamicContextMiddleware: async context_provider requires an async agent invocation",
            )
        return self._resolve_update(state, block)

    async def abefore_agent(
        self,
        state: DynamicContextState,
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, object] | None:
        """Resolve sync or async providers once at the async run boundary."""
        if self._context_provider is None:
            return {"dynamic_context_block": "", "dynamic_context_tail": ""}
        block = self._context_provider()
        if inspect.isawaitable(block):
            block = await block
        return self._resolve_update(state, block)

    # ---------- request projection ----------

    @staticmethod
    def _with_context(request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        head = str(request.state.get("dynamic_context_block") or "")
        tail = str(request.state.get("dynamic_context_tail") or "")
        if not head and not tail:
            return request
        messages = list(request.messages)
        if head:
            messages.insert(
                0,
                SystemMessage(content=head, additional_kwargs={"noesis_late_context": _HEAD_MARKER}),
            )
        if tail:
            messages.append(
                SystemMessage(content=tail, additional_kwargs={"noesis_late_context": _TAIL_MARKER}),
            )
        return request.override(messages=messages)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(self._with_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self._with_context(request))


__all__ = [
    "DynamicContextBlock",
    "DynamicContextMiddleware",
    "DynamicContextProvider",
    "DynamicContextState",
    "HEAD_TEMPLATE",
    "PRIVATE_STATE_KEYS",
    "render_head_block",
]
