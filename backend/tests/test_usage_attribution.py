"""Run usage attribution contracts (task 3.2–3.3).

Verifies per-model-call usage is attributed to caller (lead_agent/subagent)
and model_id, deduplicated by model run id, with sub-agent counted into the
run total exactly once and bounded steps.
"""

from __future__ import annotations

from noesis_server.domain.chat.streaming.usage_attribution import (
    CALLER_LEAD_AGENT,
    CALLER_SUBAGENT,
    MAX_STEPS,
    ModelCallAttribution,
    RunUsageCollector,
    resolve_caller,
)


def test_resolve_caller_from_parent_task() -> None:
    """有 parent_tool_call_id → subagent；无 → lead_agent。"""
    assert resolve_caller(None) == CALLER_LEAD_AGENT
    assert resolve_caller("") == CALLER_LEAD_AGENT
    assert resolve_caller("task-call-1") == CALLER_SUBAGENT


def test_collector_records_cumulative_and_by_caller() -> None:
    """主 Agent + 子 Agent 各一次 model call：cumulative 求和，by_caller 分别报告。"""
    collector = RunUsageCollector()
    collector.record(
        {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        attribution=ModelCallAttribution(model_run_id="run-lead", caller=CALLER_LEAD_AGENT),
    )
    collector.record(
        {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
        attribution=ModelCallAttribution(
            model_run_id="run-sub",
            caller=CALLER_SUBAGENT,
            parent_tool_call_id="task-1",
        ),
    )

    summary = collector.summary()
    assert summary["cumulative"]["input_tokens"] == 150
    assert summary["cumulative"]["output_tokens"] == 30
    assert summary["cumulative"]["total_tokens"] == 180
    assert summary["by_caller"][CALLER_LEAD_AGENT]["input_tokens"] == 100
    assert summary["by_caller"][CALLER_SUBAGENT]["input_tokens"] == 50


def test_collector_deduplicates_same_model_run_id() -> None:
    """同一 model_run_id 重复记录（流式 chunk + end）只计一次。"""
    collector = RunUsageCollector()
    attr = ModelCallAttribution(model_run_id="run-dup", caller=CALLER_LEAD_AGENT)
    first = collector.record({"input_tokens": 100, "output_tokens": 20}, attribution=attr)
    second = collector.record({"input_tokens": 100, "output_tokens": 20}, attribution=attr)

    assert first is not None
    assert second is None  # 去重
    assert collector.summary()["cumulative"]["input_tokens"] == 100


def test_collector_by_model_aggregation() -> None:
    """按 model_id 汇总：同一模型多次调用求和。"""
    collector = RunUsageCollector()
    collector.record(
        {"input_tokens": 100, "output_tokens": 20},
        attribution=ModelCallAttribution(model_run_id="r1", caller=CALLER_LEAD_AGENT, model_id="flash"),
    )
    collector.record(
        {"input_tokens": 200, "output_tokens": 40},
        attribution=ModelCallAttribution(model_run_id="r2", caller=CALLER_LEAD_AGENT, model_id="flash"),
    )
    collector.record(
        {"input_tokens": 50, "output_tokens": 10},
        attribution=ModelCallAttribution(model_run_id="r3", caller=CALLER_SUBAGENT, model_id="big-pickle"),
    )

    summary = collector.summary()
    assert summary["by_model"]["flash"]["input_tokens"] == 300
    assert summary["by_model"]["big-pickle"]["input_tokens"] == 50


def test_collector_steps_bounded() -> None:
    """steps 有上限：超过 MAX_STEPS 后不再追加，但 cumulative 继续累计。"""
    collector = RunUsageCollector()
    for i in range(MAX_STEPS + 50):
        collector.record(
            {"input_tokens": 1, "output_tokens": 1},
            attribution=ModelCallAttribution(model_run_id=f"run-{i}", caller=CALLER_LEAD_AGENT),
        )
    assert len(collector.steps) == MAX_STEPS
    # cumulative 仍累计全部
    assert collector.summary()["cumulative"]["input_tokens"] == MAX_STEPS + 50


def test_collector_anonymous_run_id_when_missing() -> None:
    """无 run_id 时生成稳定匿名 id，不互相覆盖。"""
    collector = RunUsageCollector()
    collector.record(
        {"input_tokens": 10, "output_tokens": 2},
        attribution=ModelCallAttribution(model_run_id="", caller=CALLER_LEAD_AGENT),
    )
    collector.record(
        {"input_tokens": 20, "output_tokens": 4},
        attribution=ModelCallAttribution(model_run_id="", caller=CALLER_LEAD_AGENT),
    )
    assert collector.summary()["cumulative"]["input_tokens"] == 30


def test_collector_preserves_details_in_by_caller() -> None:
    """detail（cache/reasoning）在 by_caller 桶里也累计。"""
    collector = RunUsageCollector()
    collector.record(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_token_details": {"cache_read": 60, "cache_write": 10},
            "output_token_details": {"reasoning": 5},
        },
        attribution=ModelCallAttribution(model_run_id="r1", caller=CALLER_LEAD_AGENT),
    )

    lead = collector.summary()["by_caller"][CALLER_LEAD_AGENT]
    assert lead["input_token_details"]["cache_read"] == 60
    assert lead["input_token_details"]["cache_write"] == 10
    assert lead["output_token_details"]["reasoning"] == 5


def test_collector_empty_summary_omits_by_caller() -> None:
    """无记录时 summary 不含 by_caller/by_model（避免空字典误导）。"""
    collector = RunUsageCollector()
    summary = collector.summary()
    assert summary == {"cumulative": {}}


def test_collector_skips_empty_usage() -> None:
    """normalize 后为空（None/空 dict）不记录。"""
    collector = RunUsageCollector()
    collector.record(None, attribution=ModelCallAttribution(model_run_id="r1", caller=CALLER_LEAD_AGENT))
    collector.record({}, attribution=ModelCallAttribution(model_run_id="r2", caller=CALLER_LEAD_AGENT))
    assert collector.summary() == {"cumulative": {}}


def test_subagent_usage_counted_once_into_run_total() -> None:
    """子 Agent usage 只计入 run 总量一次；父 task 仅引用归属，不重复累计。

    场景：主 Agent 调用 task 工具 → 子 Agent 内部 model call → 该 call 的 caller
    归为 subagent，计入 cumulative 一次，by_caller[subagent] 报告该值。
    主 Agent 自己的 model call 归 lead_agent。两者不重复。
    """
    collector = RunUsageCollector()
    # 主 Agent model call
    collector.record(
        {"input_tokens": 200, "output_tokens": 50, "total_tokens": 250},
        attribution=ModelCallAttribution(model_run_id="lead-1", caller=CALLER_LEAD_AGENT),
    )
    # 子 Agent model call（parent_task_call_id 指向 task 工具调用）
    collector.record(
        {"input_tokens": 300, "output_tokens": 80, "total_tokens": 380},
        attribution=ModelCallAttribution(
            model_run_id="sub-1",
            caller=CALLER_SUBAGENT,
            parent_tool_call_id="task-call-1",
        ),
    )

    summary = collector.summary()
    # run 总量 = 两者之和（子 Agent 计一次）
    assert summary["cumulative"]["input_tokens"] == 500
    assert summary["cumulative"]["total_tokens"] == 630
    # by_caller 分别报告，不重复
    assert summary["by_caller"][CALLER_LEAD_AGENT]["input_tokens"] == 200
    assert summary["by_caller"][CALLER_SUBAGENT]["input_tokens"] == 300
