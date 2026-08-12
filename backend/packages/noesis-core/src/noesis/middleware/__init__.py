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
