"""Noesis self-contained agent middleware (DeepAgents-style flat layout).

Each middleware here is self-contained: it depends only on factory-injected
dependencies (model, ``BackendProtocol``, token_counter, compiled subagents,
context providers, ...) and LangGraph typed/private state. No middleware in
this package imports ``noesis.runtime``, ``noesis.services`` or any concrete
agent scene, and none calls them at runtime.

DeepAgents/LangChain public middleware are imported from their packages and
*not* copied here. This package only holds Noesis implementations for
behaviour that differs from upstream or is missing entirely.
"""

from __future__ import annotations

from noesis.middleware.dynamic_context_middleware import (
    DynamicContextBlock,
    DynamicContextMiddleware,
    DynamicContextProvider,
    render_dynamic_block,
)
from noesis.middleware.safe_model_retry_middleware import SafeModelRetryMiddleware
from noesis.middleware.snip_middleware import (
    SnipError,
    SnipMiddleware,
    SnipRecord,
    SnipSelector,
    apply_snip_projection,
)
from noesis.middleware.tool_failure_middleware import ToolFailureMiddleware
from noesis.middleware.tool_result_budget_middleware import (
    ReplacementRecord,
    ToolResultBudgetMiddleware,
)

__all__ = [
    "DynamicContextBlock",
    "DynamicContextMiddleware",
    "DynamicContextProvider",
    "ReplacementRecord",
    "SafeModelRetryMiddleware",
    "SnipError",
    "SnipMiddleware",
    "SnipRecord",
    "SnipSelector",
    "ToolFailureMiddleware",
    "ToolResultBudgetMiddleware",
    "apply_snip_projection",
    "render_dynamic_block",
]
