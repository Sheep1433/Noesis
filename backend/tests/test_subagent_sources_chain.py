"""跨边界来源传递链回归：子任务来源清单 SHALL 无损到达主消息落库。

钉住 drb-0478 实测缺陷：桥接层登记 290 条（日志铁证），主消息落库只剩
30/任务——截断发生在 RunProjection 消费 retrieval-results-available 帧
重建 parts 时，register_retrieval_results 缺省落 max_results_per_call(30)。
链路各环节（组件级）逐一钉住，任何一环回退即红。
"""

from __future__ import annotations

from noesis.agents.background import notifications
from noesis.chat.delivery.events import WireFrame
from noesis.chat.event_mapping.retrieval import (
    MAX_CROSS_BOUNDARY_SOURCES,
    extract_deduped_sources,
    register_cross_boundary_sources,
    register_pending_sources,
    source_identity,
)
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.runs.models import RunStatus
from noesis.chat.runs.projection import RunProjection


def _web_result(i: int) -> dict:
    return {
        "source_type": "web",
        "url": f"https://example.com/page-{i}",
        "title": f"页面 {i}",
        "excerpt": "x",
    }


def test_turn_projection_merge_accumulates_across_turns():
    """多 turn 各自独立 builder：task.retrieval_sources 跨 turn 累积不丢失。"""
    from noesis.agents.background.subagent.kernel import _merge_task_sources
    from noesis.agents.background.jobs.state import BackgroundTask

    task = BackgroundTask(
        task_id="t1", session_id="sid", user_id="u1", description="调研", prompt="",
    )
    # turn 1：40 条（两个 part，模拟两次搜索各 20 条）
    b1 = AssistantMessageBuilder(session_id="c1", message_id="m1")
    b1.register_retrieval_results(tool_call_id="c1", query="q", results=[_web_result(i) for i in range(20)])
    b1.register_retrieval_results(tool_call_id="c2", query="q", results=[_web_result(i) for i in range(20, 40)])
    _merge_task_sources(task, b1.to_dict())
    # turn 2：20 条（独立 builder 从零累积）
    b2 = AssistantMessageBuilder(session_id="c1", message_id="m1")
    b2.register_retrieval_results(tool_call_id="c3", query="q", results=[_web_result(i) for i in range(40, 60)])
    _merge_task_sources(task, b2.to_dict())
    assert len(task.retrieval_sources) == 60


def test_notification_chain_carries_full_list():
    """通知 record → 注入登记：60 条全程不截（30 指纹检验）。"""
    sources = [_web_result(i) for i in range(60)]
    notifications.record(
        session_id="chain-sid", task_id="t1", status="completed",
        preview="done", label="调研", sources=sources,
    )
    notices = notifications.take_undelivered("chain-sid")
    assert len(notices[0]["sources"]) == 60

    for notice in notices:
        s = notice.get("sources")
        if isinstance(s, list) and s:
            register_pending_sources("chain-sid", str(notice.get("label") or ""), s)

    builder = AssistantMessageBuilder(session_id="sid", message_id="m-main")
    parts = register_cross_boundary_sources(builder, "chain-sid")
    assert len(parts) == 1
    assert len(parts[0].results) == 60, "跨边界登记不得截断为 max_results_per_call(30)"
    assert parts[0].origin == {"kind": "subagent", "label": "调研"}


def test_extract_deduped_sources_full_from_content():
    """子会话落库消息 content → 去重清单：完整提取。"""
    child = AssistantMessageBuilder(session_id="c1", message_id="m1")
    for start in range(0, 60, 10):
        child.register_retrieval_results(
            tool_call_id=f"call-{start}", query="q",
            results=[_web_result(i) for i in range(start, start + 10)],
        )
    deduped = extract_deduped_sources(child.to_dict())
    assert len(deduped) == 60
    assert len({source_identity(s) for s in deduped}) == 60


def test_projection_rebuild_keeps_cross_boundary_sources():
    """RunProjection 消费跨边界检索帧：重建 parts 沿用任务级上界，不落调用级 30。

    drb-0478 根因钉子：帧携带 60 条 subagent 来源，投影重建缺省
    max_results_per_call=30 截断——修复后 SHALL 沿用 MAX_CROSS_BOUNDARY_SOURCES。
    """
    projection = RunProjection(
        run_id="r1", user_id="u1", session_id="s1",
        assistant_message_id="m1", qa_type="SUPER_AGENT_QA", origin="web",
        status=RunStatus.RUNNING, attempt_id=1,
    )
    frame = WireFrame(
        event="retrieval-results-available",
        data={
            "type": "retrieval-results-available",
            "tool_call_id": "subagent-sources-abc",
            "query": "调研",
            "results": [_web_result(i) for i in range(60)],
            "origin": {"kind": "subagent", "label": "调研"},
        },
    )
    projection.apply(frame)
    snapshot = projection.snapshot(0, RunStatus.RUNNING, 1)
    parts = [p for p in snapshot.parts if p.get("type") == "retrieval"]
    assert len(parts) == 1
    assert len(parts[0]["results"]) == 60, (
        "跨边界帧重建不得截断为 max_results_per_call(30)"
    )


def test_projection_rebuild_normal_frame_keeps_call_limit():
    """普通检索帧（主 Agent 自检索）重建保持调用级上界：投影不放大常规落库体积。"""
    projection = RunProjection(
        run_id="r2", user_id="u1", session_id="s1",
        assistant_message_id="m2", qa_type="SUPER_AGENT_QA", origin="web",
        status=RunStatus.RUNNING, attempt_id=1,
    )
    frame = WireFrame(
        event="retrieval-results-available",
        data={
            "type": "retrieval-results-available",
            "tool_call_id": "call-1",
            "query": "q",
            "results": [_web_result(i) for i in range(60)],
        },
    )
    projection.apply(frame)
    snapshot = projection.snapshot(0, RunStatus.RUNNING, 1)
    parts = [p for p in snapshot.parts if p.get("type") == "retrieval"]
    assert len(parts[0]["results"]) <= MAX_CROSS_BOUNDARY_SOURCES
    # 无 origin 的普通帧沿用缺省调用级上限
    assert len(parts[0]["results"]) == 30
