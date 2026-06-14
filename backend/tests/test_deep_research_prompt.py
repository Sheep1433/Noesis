"""深度研究 system prompt 结构回归。"""

from agent.prompts import PromptProfile, build_prompt


def test_deep_research_prompt_orchestration_sections():
    prompt = build_prompt(PromptProfile.DEEP_RESEARCH)
    for section in (
        "<orchestration>",
        "write_todos",
        "research-worker",
        "report.md",
        "证据等级",
    ):
        assert section in prompt


def test_deep_research_sub_prompt_deliverable():
    prompt = build_prompt(PromptProfile.DEEP_RESEARCH_SUB)
    assert "结构化小结" in prompt
    assert "证据等级" in prompt
