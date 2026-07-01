"""超级智能体 system prompt 结构回归。"""

from agent.prompts.super_agent import NOESIS_SKILLS_SYSTEM_PROMPT
from agent.prompts import PromptProfile, build_prompt


def test_super_agent_execution_discipline():
    prompt = build_prompt(PromptProfile.SUPER_AGENT)
    for section in (
        "<task_completion>",
        "<tool_use_enforcement>",
        "<parallel_tool_calls>",
        "<interaction>",
        "禁止调用任何工具",
        "<approach>",
        "默认轻量",
        "task-worker",
        "<skills>",
        "Available Skills",
        "明确一致",
    ):
        assert section in prompt


def test_super_agent_no_mandatory_research_orchestration():
    prompt = build_prompt(PromptProfile.SUPER_AGENT)
    assert "deep-research-v2" not in prompt
    assert "<orchestration>" not in prompt
    assert "调研类" not in prompt
    assert "深度调研" not in prompt
    assert "BrowseComp" not in prompt
    assert "明确一致" in prompt


def test_noesis_skills_system_prompt_has_required_slots():
    for slot in ("{skills_locations}", "{skills_load_warnings}", "{skills_list}"):
        assert slot in NOESIS_SKILLS_SYSTEM_PROMPT
    assert "明确一致" in NOESIS_SKILLS_SYSTEM_PROMPT


def test_super_agent_sub_prompt_deliverable():
    prompt = build_prompt(PromptProfile.SUPER_AGENT_SUB)
    assert "结构化小结" in prompt
    assert "建议主 Agent 下一步" in prompt
