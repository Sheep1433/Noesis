"""超级智能体 system prompt 结构回归（约束下沉后的新契约）。

静态 prompt 只保留身份、交付真实性、轻量默认值与委派判据；
工具专属规则下沉到工具描述（fs_hints），Skills 指导由运行时注入。
"""

from noesis.agents.prompts.super_agent import NOESIS_SKILLS_SYSTEM_PROMPT
from noesis.agents.prompts import PromptProfile, build_prompt
from noesis.agents.tools.fs_hints import augment_filesystem_tool_descriptions


def test_super_agent_prompt_keeps_core_sections():
    prompt = build_prompt(PromptProfile.SUPER_AGENT)
    for section in (
        "<role>",
        "<task_completion>",
        "<approach>",
        "<task_delegation>",
        "<citations>",
        "<core>",
        "<output>",
    ):
        assert section in prompt


def test_super_agent_prompt_keeps_delegation_criteria():
    prompt = build_prompt(PromptProfile.SUPER_AGENT)
    assert "委派判据" in prompt
    assert "可并行的重子线" in prompt
    assert "run_in_background=true" in prompt
    assert "委派即隔离" in prompt
    assert "[系统通知]" in prompt
    assert "check_task" in prompt


def test_start_task_defaults_to_foreground_wait():
    """默认前台等待：后台只在超时自动转入或显式 run_in_background=true 时发生。"""
    from noesis.agents.subagents.executor import BackgroundTaskExecutor
    from noesis.agents.subagents.registry import SubagentRegistry
    from noesis.agents.subagents.tools_middleware import (
        NoesisSubagentMiddleware,
        _StartTaskArgs,
    )

    assert _StartTaskArgs.model_fields["run_in_background"].default is False
    middleware = NoesisSubagentMiddleware(
        registry=SubagentRegistry(),
        executor=BackgroundTaskExecutor(task_timeout_seconds=30),
        session_id="s",
        user_id="u",
    )
    start = next(tool for tool in middleware.tools if tool.name == "start_task")
    assert "默认 false" in start.description
    assert "自动转后台" in start.description


def test_super_agent_prompt_removed_sections():
    prompt = build_prompt(PromptProfile.SUPER_AGENT)
    for section in (
        "<interaction>",
        "<thinking>",
        "<tool_use_enforcement>",
        "<parallel_tool_calls>",
        "<model_operational>",
        "<skills>",
        "<subagent_types>",
    ):
        assert section not in prompt
    # Skills 指导由 SkillsMiddleware 运行时注入，不在静态 prompt 中
    assert "Available Skills" not in prompt


def test_noesis_skills_system_prompt_has_required_slots():
    for slot in ("{skills_locations}", "{skills_load_warnings}", "{skills_list}"):
        assert slot in NOESIS_SKILLS_SYSTEM_PROMPT
    assert "明确一致" in NOESIS_SKILLS_SYSTEM_PROMPT


def test_super_agent_sub_prompt_deliverable():
    prompt = build_prompt(PromptProfile.SUPER_AGENT_SUB)
    assert "结构化小结" in prompt
    assert "建议主 Agent 下一步" in prompt
    # 防重复检索的纪律保留
    assert "停止检索" in prompt
    assert "/memory/" in prompt


class _FakeTool:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description


class _FakeMiddleware:
    def __init__(self, tools):
        self.tools = tools


def test_fs_hints_sink_operational_rules():
    tools = [
        _FakeTool("execute"),
        _FakeTool("edit_file"),
        _FakeTool("write_file"),
        _FakeTool("read_file"),
    ]
    augment_filesystem_tool_descriptions(_FakeMiddleware(tools))
    by_name = {tool.name: tool.description for tool in tools}
    assert "cwd=/workspace" in by_name["execute"]
    assert "&&" in by_name["execute"]
    assert "read_file" in by_name["edit_file"]
    assert "/workspace/research/" in by_name["write_file"]
    assert by_name["read_file"] == ""


def test_fs_hints_idempotent_and_tolerates_missing_tools():
    augment_filesystem_tool_descriptions(_FakeMiddleware([_FakeTool("grep")]))
    tool = _FakeTool("execute", "base description")
    middleware = _FakeMiddleware([tool])
    augment_filesystem_tool_descriptions(middleware)
    once = tool.description
    augment_filesystem_tool_descriptions(middleware)
    assert tool.description == once
