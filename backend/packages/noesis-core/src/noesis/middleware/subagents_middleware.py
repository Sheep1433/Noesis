"""Sub-agent context policy — isolated / fork / resume.

Owns the context *input* policy for sub-agents: what state a sub-agent receives
from its parent. The actual sub-agent compilation, scheduling and result
return are reused from DeepAgents ``SubAgentMiddleware`` (factory injection);
this module only decides the input state shape, because upstream
``_validate_and_prepare_state`` inherits *all* parent state (only excluding a
hardcoded 6-key set — see ``subagents.py:240-247,534-539``) and leaks Noesis
private state to children.

Design contract (``simplify-agent-context-architecture`` §13):

- ``isolated`` (default): the sub-agent receives only the task description plus
  the scene-allowed whitelist of stable context; parent conversation, file
  state, tool discovery, compaction state and the durable ledger are isolated;
- ``fork`` (explicit): copy the parent conversation snapshot and a whitelist
  of durable context; all mutable state is deep-copied so parent and child do
  not affect each other afterwards;
- ``resume``: the sub-agent restores from its own checkpoint and does NOT
  re-read the parent's current state;
- private middleware state never participates in parent↔child merge.

Self-containment: pure state-shaping logic over LangGraph state dicts. No
``runtime``/``service`` calls. The factory wires this policy into the sub-agent
task tool at assembly time; this module has no dependency on upstream subagent
internals beyond the documented excluded-key contract.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


# Keys that upstream excludes plus the Noesis private-state keys that must
# never leak to an isolated child. This is the Noesis-side extension of the
# upstream ``_EXCLUDED_STATE_KEYS`` contract.
PARENT_ONLY_KEYS: frozenset[str] = frozenset({
    # upstream excluded
    "messages", "todos", "structured_response",
    "skills_metadata", "skills_load_errors", "memory_contents",
    # Noesis private state
    "_source_revision", "_durable_context", "_file_context",
    "_snip_records", "_micro_compaction_records",
    "_tool_result_replacements", "_tool_catalog_discovered",
    "_summarization_event",
})


class SubAgentContextMode(str, Enum):
    """Context mode for a sub-agent invocation."""

    ISOLATED = "isolated"
    FORK = "fork"
    RESUME = "resume"


@dataclass(frozen=True)
class SubAgentContextPolicy:
    """Per-subagent context policy resolved at factory time."""

    mode: SubAgentContextMode = SubAgentContextMode.ISOLATED
    # Whitelist of stable-context state keys the child may inherit in
    # isolated/fork mode (e.g. scene-allowed skills refs).
    stable_context_whitelist: tuple[str, ...] = ()
    # Whitelist of durable-context fields the child may copy in fork mode.
    durable_whitelist: tuple[str, ...] = ()
    # Sub-agent's own checkpoint key (for resume mode).
    subagent_thread_id: str | None = None


def prepare_isolated_state(
    parent_state: dict[str, Any],
    description: str,
    policy: SubAgentContextPolicy,
) -> dict[str, Any]:
    """Build an isolated sub-agent input state.

    The child receives only the task description (as a fresh ``messages``
    list) plus the scene-allowed stable-context keys explicitly whitelisted.
    Everything else — conversation, file state, compaction state, durable
    ledger — is isolated.
    """
    child: dict[str, Any] = {}
    for key in policy.stable_context_whitelist:
        if key in parent_state and key not in PARENT_ONLY_KEYS:
            child[key] = copy.deepcopy(parent_state[key])
    child["messages"] = [HumanMessage(content=description)]
    return child


def prepare_fork_state(
    parent_state: dict[str, Any],
    description: str,
    policy: SubAgentContextPolicy,
) -> dict[str, Any]:
    """Build a forked sub-agent input state.

    Copies the parent conversation snapshot plus the whitelisted durable
    context, deep-copying every mutable value so the child cannot mutate the
    parent's state. Private middleware state is still excluded.
    """
    child: dict[str, Any] = {}
    # Conversation snapshot (deep-copied so the child mutating messages does
    # not touch the parent transcript).
    parent_messages = list(parent_state.get("messages", []))
    child["messages"] = [HumanMessage(content=description), *copy.deepcopy(parent_messages)]

    # Whitelisted durable context (deep copy).
    durable = parent_state.get("_durable_context")
    if isinstance(durable, dict) and policy.durable_whitelist:
        forked_durable: dict[str, Any] = {}
        for field_name in policy.durable_whitelist:
            if field_name in durable:
                forked_durable[field_name] = copy.deepcopy(durable[field_name])
        if forked_durable:
            child["_durable_context"] = forked_durable

    # No other private state is copied — parent-only keys stay parent-only.
    return child


def prepare_resume_state(
    parent_state: dict[str, Any],  # noqa: ARG001 — intentionally unused
    description: str | None,
    policy: SubAgentContextPolicy,
) -> dict[str, Any]:
    """Build a resume sub-agent input state.

    The sub-agent restores from its own checkpoint; it does NOT read the
    parent's current state. Only a minimal seed is returned — the actual
    history comes from the child's own checkpointer keyed by
    ``subagent_thread_id``.
    """
    if not policy.subagent_thread_id:
        raise ValueError("resume mode requires subagent_thread_id in the policy")
    # The caller (task tool) wires this to the child's checkpointer config so
    # the child graph restores its own messages; we only assert the contract.
    seed: dict[str, Any] = {}
    if description is not None:
        seed["messages"] = [HumanMessage(content=description)]
    return seed


def prepare_subagent_state(
    parent_state: dict[str, Any],
    description: str,
    policy: SubAgentContextPolicy,
) -> dict[str, Any]:
    """Dispatch to the policy-mode-specific state preparer."""
    if policy.mode is SubAgentContextMode.ISOLATED:
        return prepare_isolated_state(parent_state, description, policy)
    if policy.mode is SubAgentContextMode.FORK:
        return prepare_fork_state(parent_state, description, policy)
    if policy.mode is SubAgentContextMode.RESUME:
        return prepare_resume_state(parent_state, description, policy)
    raise ValueError(f"unknown sub-agent context mode: {policy.mode}")


class SubAgentContextMiddleware:
    """Context-policy holder for sub-agent invocations.

    This is a lightweight policy registry, not an ``AgentMiddleware`` with
    model/tool-call seams: the actual seam is the sub-agent ``task`` tool,
    which the factory wires to call :func:`prepare_subagent_state`. It is
    kept as a class so per-subagent policies can be registered and looked up
    by sub-agent name at invocation time.
    """

    def __init__(self, default_policy: SubAgentContextPolicy | None = None) -> None:
        self._default = default_policy or SubAgentContextPolicy()
        self._policies: dict[str, SubAgentContextPolicy] = {}

    def register(self, subagent_name: str, policy: SubAgentContextPolicy) -> None:
        self._policies[subagent_name] = policy

    def policy_for(self, subagent_name: str) -> SubAgentContextPolicy:
        return self._policies.get(subagent_name, self._default)

    def prepare_state(
        self,
        subagent_name: str,
        parent_state: dict[str, Any],
        description: str,
    ) -> dict[str, Any]:
        return prepare_subagent_state(parent_state, description, self.policy_for(subagent_name))


__all__ = [
    "PARENT_ONLY_KEYS",
    "SubAgentContextMiddleware",
    "SubAgentContextMode",
    "SubAgentContextPolicy",
    "prepare_fork_state",
    "prepare_isolated_state",
    "prepare_resume_state",
    "prepare_subagent_state",
]
