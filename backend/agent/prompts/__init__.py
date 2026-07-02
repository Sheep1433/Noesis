"""Noesis Agent system prompt 入口。

分区顺序按 prefix cache 友好性排列（静 → 动）：
core/thinking/output 见 base；各场景扩展见独立模块。
"""

from __future__ import annotations

from enum import StrEnum

from agent.prompts.common_qa import build_common_qa_prompt
from agent.prompts.fault_operation import build_fault_operation_prompt, build_fault_operation_sub_prompt
from agent.prompts.simple_mcp import build_simple_mcp_prompt
from agent.prompts.super_agent import build_super_agent_prompt, build_super_agent_sub_prompt


class PromptProfile(StrEnum):
    COMMON_QA = "common_qa"
    FAULT_OPERATION = "fault_operation"
    FAULT_OPERATION_SUB = "fault_operation_sub"
    SUPER_AGENT = "super_agent"
    SUPER_AGENT_SUB = "super_agent_sub"
    SIMPLE_MCP = "simple_mcp"


def build_prompt(
    profile: PromptProfile | str,
    *,
    kb_enabled: bool = False,
    attachments_enabled: bool = False,
    kb_scope_collections: list[str] | None = None,
    user_id: str | None = None,
) -> str:
    """按 profile 组装 system prompt。common_qa 支持 kb_enabled / attachments_enabled 扩展段。"""
    key = profile.value if isinstance(profile, PromptProfile) else profile

    builders = {
        PromptProfile.COMMON_QA.value: lambda: build_common_qa_prompt(
            kb_enabled=kb_enabled,
            attachments_enabled=attachments_enabled,
            kb_scope_collections=kb_scope_collections,
        ),
        PromptProfile.FAULT_OPERATION.value: build_fault_operation_prompt,
        PromptProfile.FAULT_OPERATION_SUB.value: build_fault_operation_sub_prompt,
        PromptProfile.SUPER_AGENT.value: build_super_agent_prompt,
        PromptProfile.SUPER_AGENT_SUB.value: build_super_agent_sub_prompt,
        PromptProfile.SIMPLE_MCP.value: build_simple_mcp_prompt,
    }

    builder = builders.get(key)
    if builder is None:
        raise ValueError(f"unknown prompt profile: {key!r}")
    return builder()


__all__ = ["PromptProfile", "build_prompt"]
