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


@pytest.mark.integration
def test_super_agent_web_answer_persists_clickable_citation(
    auth_client,
    create_session,
    create_run,
    collect_run_stream,
):
    """智能体直接 Web 检索后，实时流和终态消息都保留可点击引用。"""
    session_id = create_session(
        title="api-test-super-agent-citation",
        qa_type="SUPER_AGENT_QA",
    )
    run = create_run(
        session_id=session_id,
        content=(
            "请调用 web_fetch 抓取 https://api.github.com，"
            "用一句话告诉我响应中 current_user_url 的值，并把结论绑定到该网页来源。"
        ),
        qa_type="SUPER_AGENT_QA",
    )

    events = collect_run_stream(
        auth_client,
        run["run_id"],
        deadline_seconds=DEEP_RESEARCH_DEADLINE,
    )
    retrieval_events = [
        payload for name, payload in events.events
        if name == "retrieval-results-available" and isinstance(payload, dict)
    ]
    annotation_events = [
        payload for name, payload in events.events
        if name == "text-annotation-added" and isinstance(payload, dict)
    ]

    assert events.succeeded, f"SSE 未成功结束: {events.error}"
    assert "web_fetch" in events.tool_names, f"未调用 web_fetch: {events.tool_names}"
    assert retrieval_events and retrieval_events[0].get("results"), "实时流缺少 retrieval results"
    assert annotation_events, "实时流缺少 text annotation"

    message_id = annotation_events[0].get("message_id")
    response = auth_client.get(f"/api/chat/messages/{message_id}")
    response.raise_for_status()
    content = response.json()["data"]["content"]
    parts = content["parts"]
    assert any(part.get("type") == "retrieval" for part in parts), "终态消息丢失 retrieval part"
    assert any(
        annotation.get("type") == "url_citation"
        for part in parts if part.get("type") == "text"
        for annotation in part.get("annotations") or []
    ), "终态消息缺少 url_citation"
