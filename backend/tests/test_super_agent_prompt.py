"""超级智能体 system prompt 结构回归。"""

from agent.prompts import PromptProfile, build_prompt
from agent.prompts.skills_index import build_skills_index_prompt


def test_super_agent_execution_discipline():
    prompt = build_prompt(PromptProfile.SUPER_AGENT)
    for section in (
        "<task_completion>",
        "<tool_use_enforcement>",
        "<parallel_tool_calls>",
        "<interaction>",
        "禁止调用任何工具",
        "task-worker",
        "<skills_index>",
    ):
        assert section in prompt


def test_super_agent_skills_index_lists_platform_skills():
    index = build_skills_index_prompt()
    assert "deep-research-v2" in index
    assert "/skills/extensions/" in index


def test_super_agent_sub_prompt_deliverable():
    prompt = build_prompt(PromptProfile.SUPER_AGENT_SUB)
    assert "结构化小结" in prompt
    assert "建议主 Agent 下一步" in prompt
