"""简单 MCP 调试 Agent system prompt。"""

from __future__ import annotations

from agent.prompts.base import build_base_prompt

_ROLE = """<role>
你是 MCP 智能助手，通过 MCP 工具调用远程服务。
</role>"""

_RULES = """<rules>
仅使用提供的 MCP 工具；不编造命令或数据，只返回实际执行结果。
</rules>"""


def build_simple_mcp_prompt() -> str:
    return build_base_prompt(_ROLE, _RULES)
