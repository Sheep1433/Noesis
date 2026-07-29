"""接口测试：SUPER_AGENT_QA 真实深度研究链路（含 sandbox / web / execute 工具）。

前置：本地起后端 + sandbox-runner 可用（``sandbox.backend=docker``）。

    cd backend && uv run app.py
    uv run pytest tests/api/test_super_agent_real_llm.py -m integration -s
"""

from __future__ import annotations

import pytest

# 深度研究可能跑较久，给足上限
DEEP_RESEARCH_DEADLINE = 600


@pytest.mark.integration
def test_super_agent_curl_fetch(auth_client, create_session, create_run, collect_run_stream):
    """SuperAgent 用 execute 工具执行 curl 抓取任意外部 URL，并回报内容。"""
    session_id = create_session(title="api-test-curl", qa_type="SUPER_AGENT_QA")
    run = create_run(
        session_id=session_id,
        content=(
            "请使用 execute 工具执行命令 `curl -s https://example.com`，"
            "然后把返回 HTML 中 <title> 标签的内容告诉我。"
        ),
        qa_type="SUPER_AGENT_QA",
    )

    events = collect_run_stream(auth_client, run["run_id"], deadline_seconds=DEEP_RESEARCH_DEADLINE)
    print(f"tool_names={events.tool_names} finish_reason={events.finish_reason} "
          f"error={events.error} text_len={len(events.text)}")

    # 1. 真的调了 execute 工具（shell 执行）
    assert "execute" in events.tool_names, f"未调用 execute 工具: {events.tool_names}"
    # 2. execute 的输出成功返回，且包含 example.com 的页面标题
    exec_out = events.output_for_tool("execute")
    assert exec_out is not None, "execute 工具缺少 tool-output-available"
    assert exec_out.get("status") == "success", f"execute 状态非 success: {exec_out}"
    assert exec_out.get("state") == "succeeded", f"execute 生命周期非 succeeded: {exec_out}"
    output = str(exec_out.get("output") or "")
    assert "Example Domain" in output, f"curl 输出未包含预期内容: {output[:200]}"
    # 3. 整轮成功结束并产出文本
    assert events.succeeded, f"SSE 未成功结束: {events.error}"
    assert events.text.strip(), "未产出文本"


@pytest.mark.integration
def test_super_agent_deep_research(auth_client, create_session, create_run, collect_run_stream):
    """SUPER_AGENT_QA 深度研究：多工具协同 + 长文本产出。"""
    session_id = create_session(title="api-test-deep-research", qa_type="SUPER_AGENT_QA")
    run = create_run(
        session_id=session_id,
        content="深度调研：简要对比 SQLite 与 DuckDB 的核心定位差异，给出结论。要求调用搜索工具核实。",
        qa_type="SUPER_AGENT_QA",
    )

    events = collect_run_stream(auth_client, run["run_id"], deadline_seconds=DEEP_RESEARCH_DEADLINE)
    print(f"tool_names={events.tool_names} finish_reason={events.finish_reason} "
          f"error={events.error} text_len={len(events.text)}")

    # 1. 深度研究应触发至少一种检索/执行工具
    research_tools = {"web_search", "web_fetch", "execute"}
    assert any(t in research_tools for t in events.tool_names), \
        f"未触发任何研究类工具: {events.tool_names}"
    # 2. 至少有一次工具输出成功
    assert any(o.get("status") == "success" for o in events.tool_outputs), \
        f"无成功的工具输出: {events.tool_outputs}"
    # 3. 产出有实质长度的结论文本
    assert events.succeeded, f"SSE 未成功结束: {events.error}"
    assert len(events.text) >= 50, f"结论文本过短: {events.text[:200]}"
