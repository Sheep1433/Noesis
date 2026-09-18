"""压缩评测新模块测试：gen_probes 解析 / export_session / agent_path 投影镜像。"""

import json

import pytest
from langchain_core.messages import HumanMessage

from evals.compression.export_session import extract_messages, scrub
from evals.compression.gen_probes import (
    GEN_PROMPT_VERSION,
    LAYERS,
    compacted_region,
    even_layer_split,
    parse_layer_split,
    parse_probes_response,
    probe_bank_is_current,
    sample_region_text,
    transcript_sha,
)


# ---------------------------------------------------------------- gen_probes

def test_compacted_region_keeps_recent():
    messages = [{"type": "human", "content": str(i)} for i in range(10)]
    region = compacted_region(messages, keep_n=3)
    assert len(region) == 7
    assert region[0]["content"] == "0"


_EVEN = {name: 1 for name in LAYERS}


def test_parse_layer_split_and_even_split():
    assert parse_layer_split("10:5:5") == {"macro": 10, "meso": 5, "detail": 5}
    with pytest.raises(ValueError, match="3 段"):
        parse_layer_split("10:5")
    with pytest.raises(ValueError, match="非整数"):
        parse_layer_split("10:x:5")
    with pytest.raises(ValueError, match="≥1"):
        parse_layer_split("10:0:5")
    assert even_layer_split(12) == {"macro": 4, "meso": 4, "detail": 4}
    # 余数从首层起 +1
    assert even_layer_split(5) == {"macro": 2, "meso": 2, "detail": 1}
    with pytest.raises(ValueError, match="不足以三层"):
        even_layer_split(2)


def test_parse_probes_response_per_layer_quota_and_truncation():
    # macro 超配额取前 1 题；meso/detail 各 1 题
    raw = ('[{"id": "p1", "layer": "macro", "question": "q1", "reference_answer": "a"},'
           '{"id": "p2", "layer": "macro", "question": "q2", "reference_answer": "a"},'
           '{"id": "p3", "layer": "meso", "question": "q3", "reference_answer": "a"},'
           '{"id": "p4", "layer": "detail", "question": "q4", "reference_answer": "a"}]')
    probes = parse_probes_response(raw, layer_counts=_EVEN)
    assert [p["id"] for p in probes] == ["p1", "p3", "p4"]
    assert {p["layer"] for p in probes} == {"macro", "meso", "detail"}
    with pytest.raises(ValueError):
        parse_probes_response("not json", layer_counts=_EVEN)


def test_parse_probes_response_rejects_missing_or_unknown_layer():
    with pytest.raises(ValueError, match="layer"):
        parse_probes_response('[{"id": "p1", "question": "q"}]', layer_counts=_EVEN)
    with pytest.raises(ValueError, match="layer"):
        parse_probes_response('[{"id": "p1", "layer": "nano", "question": "q"}]',
                              layer_counts=_EVEN)


def test_parse_probes_response_rejects_under_quota():
    raw = ('[{"id": "p1", "layer": "macro", "question": "q", "reference_answer": "a"},'
           '{"id": "p2", "layer": "macro", "question": "q", "reference_answer": "a"},'
           '{"id": "p3", "layer": "detail", "question": "q", "reference_answer": "a"}]')
    # meso 缺层 → 配额不足拒绝
    with pytest.raises(ValueError, match="配额不足"):
        parse_probes_response(raw, layer_counts=_EVEN)
    # 关闭分层约束（小题量场景）→ 放行；macro 仍截到配额 1 题
    probes = parse_probes_response(raw, layer_counts=_EVEN, require_layers=False)
    assert [p["id"] for p in probes] == ["p1", "p3"]


def test_probe_bank_is_current_checks_version_and_sha():
    sha = transcript_sha([{"type": "human", "content": "x"}])
    # 手写题库（无 sha）冻结可复用
    assert probe_bank_is_current({"transcript_sha256": None}, sha) is True
    # 机器生成：sha 一致但 prompt 版本旧 → 重新生成
    assert probe_bank_is_current(
        {"transcript_sha256": sha, "gen_prompt_version": "gen-probes/v1"}, sha
    ) is False
    # sha 一致且版本一致 → 缓存命中
    assert probe_bank_is_current(
        {"transcript_sha256": sha, "gen_prompt_version": GEN_PROMPT_VERSION}, sha
    ) is True
    # transcript 变化 → 重新生成
    assert probe_bank_is_current(
        {"transcript_sha256": "other", "gen_prompt_version": GEN_PROMPT_VERSION}, sha
    ) is False


def test_sample_region_text_even_coverage():
    text = "".join(f"seg{i:03d}" + "x" * 900 for i in range(200))  # ~182K chars
    out = sample_region_text(text, budget=60_000)
    assert len(out) <= 70_000
    assert "seg000" in out and "seg100" in out and "seg199" in out  # 首中尾都覆盖
    short = sample_region_text("abc", budget=60_000)
    assert short == "abc"


def test_transcript_sha_stable_and_sensitive():
    msgs = [{"type": "human", "content": "x"}]
    assert transcript_sha(msgs) == transcript_sha([{"content": "x", "type": "human"}])
    assert transcript_sha(msgs) != transcript_sha([{"type": "human", "content": "y"}])


# ---------------------------------------------------------------- export_session

def _assistant_event(blocks, **extra):
    return {"type": "assistant", "message": {"content": blocks}, **extra}


def _user_text_event(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def test_extract_messages_pairs_tool_use_and_result():
    events = [
        _user_text_event("跑一下测试"),
        _assistant_event([
            {"type": "text", "text": "我来执行"},
            {"type": "tool_use", "id": "t1", "name": "terminal",
             "input": {"command": "pytest -q"}},
        ]),
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok output"}
        ]}},
        _assistant_event([{"type": "thinking", "thinking": "internal"}]),
        _user_text_event("<command-name>/clear</command-name>"),
        {"type": "user", "isSidechain": True, "message": {"content": "sidechain"}},
    ]
    messages = extract_messages(iter(events))
    kinds = [m["type"] for m in messages]
    assert kinds == ["human", "ai", "ai", "tool"]
    # 工具入参入 transcript
    assert "pytest -q" in messages[2]["content"]
    tool_msg = messages[3]
    assert tool_msg["name"] == "terminal"
    assert tool_msg["content"] == "ok output"


def test_scrub_replaces_sensitive_patterns():
    text = "邮箱 a@b.com，key sk-abc123def456ghi789，路径 /Users/zzq/x"
    out = scrub(text)
    assert "a@b.com" not in out and "[EMAIL]" in out
    assert "sk-abc" not in out and "[API_KEY]" in out
    assert "/Users/zzq" not in out


# ---------------------------------------------------------------- 三组对比

def test_select_probes_layer_filter_and_slice():
    from evals.compression.__main__ import select_probes

    bank = [
        {"id": "p1", "layer": "macro"},
        {"id": "p2", "layer": "macro"},
        {"id": "p3", "layer": "detail"},
        {"id": "p4", "layer": "detail"},
    ]
    assert [p["id"] for p in select_probes(bank)] == ["p1", "p2", "p3", "p4"]
    assert [p["id"] for p in select_probes(bank, layer="detail")] == ["p3", "p4"]
    assert [p["id"] for p in select_probes(bank, layer="macro", max_probes=1)] == ["p1"]
    with pytest.raises(ValueError, match="未知 layer"):
        select_probes(bank, layer="nano")
    # 旧题库（无 layer 标注）按层过滤为空 → 参数错误而非跑空题库
    with pytest.raises(ValueError, match="无题目"):
        select_probes([{"id": "p1"}], layer="detail")


# ------------------------------------------------- agent_path（真 Agent 路径）

def test_normalize_fixture_for_state_pairs_tool_calls():
    """导出件无 tool_call 配对：回填配对 tool_calls，产出生产状态形状
    （工具结果是 ToolMessage 而非用户消息——用户原话装回按角色取真用户，
    转写成 HumanMessage 会让装回预算被工具输出吃掉）。"""
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

    from evals.compression.agent_path import normalize_fixture_for_state

    out = normalize_fixture_for_state([
        SystemMessage(content="system"),
        HumanMessage(content="跑一下测试"),
        AIMessage(content="[调用工具 Bash] ls"),
        ToolMessage(content="file.txt", name="Bash", tool_call_id="t1"),
        AIMessage(content="完成"),
    ])
    assert [type(m).__name__ for m in out] == [
        "HumanMessage", "AIMessage", "ToolMessage", "AIMessage",
    ]
    assert out[1].tool_calls[0]["name"] == "Bash"
    assert out[2].tool_call_id == out[1].tool_calls[0]["id"]
    assert out[2].content == "file.txt"
    # 连续多条 tool 全部配对到同一个 AI
    more = normalize_fixture_for_state([
        AIMessage(content=""),
        ToolMessage(content="a", name="T1", tool_call_id="x"),
        ToolMessage(content="b", name="T2", tool_call_id="y"),
    ])
    assert len(more[0].tool_calls) == 2
    assert [m.tool_call_id for m in more[1:]] == [c["id"] for c in more[0].tool_calls]
    # 无前置 AI 的孤儿 tool：合成空 AI 承载配对
    orphan = normalize_fixture_for_state([
        HumanMessage(content="q"),
        ToolMessage(content="r", name="T", tool_call_id="z"),
    ])
    assert [type(m).__name__ for m in orphan] == ["HumanMessage", "AIMessage", "ToolMessage"]


def test_projected_context_injects_retained_users(monkeypatch):
    """评测侧投影镜像与生产最终请求语义一致：[用户原话, summary, 保留尾]。

    metrics 的 post_tokens / post_message_count 都从这里出——镜像漂移
    意味着报告口径漂移。
    """
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, ToolMessage

    from evals.compression.agent_path import _projected_context

    monkeypatch.setattr(
        "noesis.config.env.ModelConfig",
        SimpleNamespace(summarization_user_message_tokens=10_000),
    )
    raw = [
        HumanMessage(content="用户消息 0"),
        HumanMessage(content="用户消息 1"),
        AIMessage(content="回复"),
        ToolMessage(content="工具结果", tool_call_id="c1"),
        HumanMessage(content="用户消息 2"),
        HumanMessage(content="用户消息 3"),
        HumanMessage(content="用户消息 4"),
    ]
    summary = HumanMessage(
        content="summary",
        additional_kwargs={"lc_source": "summarization"},
    )
    post = {
        "messages": raw,
        "compaction": {"event": {"summary_message": summary, "cutoff_index": 6}},
    }
    projected, event = _projected_context(post)
    # cutoff=6：原话 = raw[:6] 里 4 条真实用户消息（AI/工具不装回），时间序在前
    assert [m.content for m in projected[:4]] == [
        "用户消息 0", "用户消息 1", "用户消息 2", "用户消息 3",
    ]
    assert projected[4] is summary
    assert projected[5:] == [raw[6]]
    assert event["cutoff_index"] == 6

    # 无压缩事件 → 原样
    assert _projected_context({"messages": raw}) == (raw, None)


def test_group_db_rows_matches_production_shape():
    """落库分组对齐消息表 v2.1：human → user 行；ai + 后续 tool 合并为一条
    assistant 行（text part + tool parts）。"""
    from evals.compression.agent_path import _group_db_rows

    rows = _group_db_rows([
        {"type": "human", "content": "查一下配置"},
        {"type": "ai", "content": "我来查"},
        {"type": "tool", "content": "max_size=0", "name": "read_file"},
        {"type": "tool", "content": "timeout=30", "name": "read_file"},
        {"type": "ai", "content": "配置如上"},
    ])
    assert rows[0] == ("user", [{"type": "text", "content": "查一下配置"}])
    assert rows[1][0] == "assistant"
    assert rows[1][1][0] == {"type": "text", "content": "我来查"}
    assert rows[1][1][1] == {"type": "tool", "name": "read_file", "input": {}, "output": "max_size=0"}
    assert rows[1][1][2] == {"type": "tool", "name": "read_file", "input": {}, "output": "timeout=30"}
    assert rows[2] == ("assistant", [{"type": "text", "content": "配置如上"}])


def test_arm_flags_single_variable_chain():
    """三组配置矩阵构成严格单变量对照链：
    uncompacted↔current 只差压缩；current↔recovery 只差会话检索。"""
    from evals.compression.agent_path import ARM_FLAGS

    assert ARM_FLAGS["recovery"] == {"history_search_enabled": True, "compaction_enabled": True}
    # 闭卷组：唯一差异 = 挂会话检索
    assert ARM_FLAGS["current"] == {"history_search_enabled": False, "compaction_enabled": True}
    # 不压缩组：与闭卷组唯一差异 = 压缩关闭（也不挂检索——上限是原生召回，
    # 混入检索会污染「压缩净损失」对照）
    assert ARM_FLAGS["uncompacted"] == {"history_search_enabled": False, "compaction_enabled": False}


def test_fork_checkpoint_copies_channel_values():
    """fork 必须复制 channel 值本体：MemorySaver 的值存于按 thread 隔离的
    blob 区（键 = thread/channel/version），new_versions 不带源版本映射时
    目标线程只落检查点骨架、消息全空（曾发生的真实 bug）。"""
    import asyncio

    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    from evals.compression.agent_path import _fork_checkpoint

    class S(TypedDict):
        messages: list

    async def main():
        saver = MemorySaver()
        graph = (
            StateGraph(S, input=S, output=S)
            .add_node("noop", lambda s: {"messages": s["messages"]})
            .add_edge(START, "noop")
            .add_edge("noop", END)
            .compile(checkpointer=saver)
        )
        seed = [HumanMessage(content=f"m{i}") for i in range(10)]
        await graph.aupdate_state(
            {"configurable": {"thread_id": "src"}}, {"messages": seed})
        await _fork_checkpoint(saver, src_thread="src", dst_thread="dst")
        state = await graph.aget_state({"configurable": {"thread_id": "dst"}})
        msgs = state.values.get("messages") or []
        assert [m.content for m in msgs] == [f"m{i}" for i in range(10)]

    asyncio.run(main())


# ---------------------------------------------------------------- __main__ CLI
def test_resolve_runs_rejects_non_integer_env(monkeypatch):
    from types import SimpleNamespace

    from evals.compression.__main__ import _resolve_runs

    monkeypatch.setenv("NOESIS_COMPRESSION_EVAL_RUNS", "abc")
    with pytest.raises(SystemExit, match="正整数"):
        _resolve_runs(SimpleNamespace(runs=None))


def test_fixture_grouped_summaries_no_cross_fixture_mixing():
    """多 fixture 汇总按 fixture 分组：每行只含该 fixture 的 run，fixture
    字段正确标注——混入一次 summarize_arm_runs 会产出跨 fixture 中位数
    且错标（终版 100 题的教训固化）。"""
    from evals.compression.__main__ import _fixture_grouped_summaries

    def payload(fid, arm, score):
        return {
            "fixture_id": fid, "arm": arm, "run_index": 0,
            "policy": {}, "eval_run_id": "t", "session_id": "s",
            "compression": {"pre_tokens": 100, "pre_message_count": 5},
            "probes": [
                {"probe_id": pid, "layer": "macro", "recall": score, "completed": True,
                 "scores": {"accuracy": 5, "artifact_trail": 5, "context_awareness": 5,
                            "continuity": 5, "completeness": 5}}
                for pid in ("p1", "p2")
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    payloads = {
        "current": [payload("fx-a", "current", 2), payload("fx-b", "current", 0)],
        "recovery": [payload("fx-a", "recovery", 2), payload("fx-b", "recovery", 2)],
    }
    rows = _fixture_grouped_summaries(payloads, ["fx-a", "fx-b"], ["current", "recovery"])
    # 2 fixture × 2 arm = 4 行，各fixture各arm 一行
    assert len(rows) == 4
    by_key = {(r["fixture_id"], r["arm"]): r for r in rows}
    assert set(by_key) == {("fx-a", "current"), ("fx-b", "current"),
                           ("fx-a", "recovery"), ("fx-b", "recovery")}
    # fx-a current = 满分 100%，fx-b current = 0——没有被彼此稀释
    assert by_key[("fx-a", "current")]["recall_pct"] == 1.0
    assert by_key[("fx-b", "current")]["recall_pct"] == 0.0
    # 缺 fixture 的 arm 组合跳过而非报错
    rows2 = _fixture_grouped_summaries({"current": [payload("fx-a", "current", 2)]},
                                       ["fx-a", "fx-b"], ["current", "recovery"])
    assert len(rows2) == 1 and rows2[0]["fixture_id"] == "fx-a"
