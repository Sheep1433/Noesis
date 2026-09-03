"""记忆召回评测测试：条目解析、检索指标、三层汇总、LongMemEval 导入（fake store）。"""

import json

import pytest

from evals.agent.memory.fixtures import NEGATIVE_QUERIES, NEGATIVE_SCENARIOS, RECALL_SCENARIOS
from evals.agent.memory.longmemeval import (
    eval_user_id,
    import_question,
    session_body,
)
from evals.agent.memory.metrics import (
    memory_accessed,
    parse_search_memory_slugs,
    retrieval_scores,
    summarize_memory_eval,
)


def _search_output(slugs):
    return [{
        "name": "search_memory",
        "output": json.dumps(
            {"results": [{"memory_type": "experience", "slug": s} for s in slugs]},
            ensure_ascii=False),
    }]


def test_parse_search_memory_slugs_in_order():
    outputs = _search_output(["s1", "s2"]) + [{"name": "read_file", "output": "x"}]
    assert parse_search_memory_slugs(outputs) == ["s1", "s2"]


def test_parse_search_memory_slugs_tolerates_bad_json():
    assert parse_search_memory_slugs([{"name": "search_memory", "output": "not json"}]) == []


def test_retrieval_scores_recall_precision():
    scores = retrieval_scores(["a", "x", "b", "a"], ["a", "b", "c"], k=5)
    assert scores["recall@k"] == pytest.approx(2 / 3)
    assert scores["precision@k"] == pytest.approx(2 / 3)  # 去重后 retrieved = [a, x, b]


def test_retrieval_scores_respects_k():
    scores = retrieval_scores(["x", "a"], ["a"], k=1)
    assert scores["recall@k"] == 0.0


def test_retrieval_scores_no_expected():
    assert retrieval_scores(["a"], [])["recall@k"] is None


def _positive(sid="q1", verdict="accepted", recall=1.0, called=True, qtype="multi-session"):
    return {
        "sample_id": sid, "negative": False, "question_type": qtype,
        "completed": True, "error": None,
        "judge": {"verdict": verdict},
        "retrieval": {"recall@k": recall, "precision@k": recall},
        "search_memory_calls": 1 if called else 0,
    }


def _negative(sid="n1", violation=False):
    return {"sample_id": sid, "negative": True, "completed": True, "error": None,
            "violation": violation, "search_memory_calls": 1 if violation else 0}


def test_memory_accessed_both_paths():
    # search_memory 工具路径
    assert memory_accessed({"search_memory_calls": 1, "tool_outputs": []}) is True
    # /memory 虚拟路径读取（read_file 入参含 /memory）
    assert memory_accessed({
        "search_memory_calls": 0,
        "tool_outputs": [{"name": "read_file", "input": {"path": "/memory/preference/x.md"}, "output": "..."}],
    }) is True
    # 无关工具调用不算
    assert memory_accessed({
        "search_memory_calls": 0,
        "tool_outputs": [{"name": "read_file", "input": {"path": "/workspace/a.py"}, "output": "..."}],
    }) is False


def test_summarize_three_layers_and_negatives():
    summary = summarize_memory_eval([
        _positive("q1", "accepted", 1.0, True),
        _positive("q2", "rejected", 0.0, False),
        _positive("q3", "accepted", 0.5, True, qtype="temporal-reasoning"),
        _negative("n1", violation=False),
        _negative("n2", violation=True),
    ])
    assert summary["positives"] == 3 and summary["negatives"] == 2
    assert summary["answer_accepted_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert summary["mean_recall@k"] == pytest.approx(0.5)
    assert summary["behavior_recall_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert summary["negative_false_recall_rate"] == pytest.approx(0.5, abs=1e-3)
    assert summary["by_question_type"]["multi-session"] == {"n": 2, "accepted": 1}


def test_summarize_error_records_do_not_pollute_judge():
    summary = summarize_memory_eval([
        {"sample_id": "e1", "negative": False, "completed": False, "error": "timeout"},
    ])
    assert summary["errors"] == 1
    assert summary["judged"] == 0


def test_session_body_formats_turns():
    body = session_body([
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在的"},
    ])
    assert "[user] 你好" in body and "[assistant] 在的" in body


def test_import_question_isolated_user_and_idempotent(monkeypatch):
    calls = []

    class FakeEntry:
        rel_path = "experience/s1.md"

    def fake_upsert(cls, user_id, **kwargs):
        calls.append((user_id, kwargs["slug"], kwargs["memory_type"]))
        return FakeEntry()

    monkeypatch.setattr(
        "evals.agent.memory.runner.MemoryStore.upsert_entry",
        classmethod(fake_upsert),
    )
    question = {
        "question_id": "qid_1",
        "sessions": [
            {"session_id": "s1", "turns": [{"role": "user", "content": "a"}]},
            {"session_id": "s2", "turns": [{"role": "assistant", "content": "b"}]},
        ],
    }
    user_id = import_question(question)
    assert user_id == eval_user_id("qid_1")
    import_question(question)  # 幂等重导入
    assert [c[1] for c in calls] == ["s1", "s2", "s1", "s2"]
    assert all(c[0] == user_id and c[2] == "experience" for c in calls)


def test_fixtures_negative_coverage():
    # 冒烟正负例成对、负例带可字面检测的 forbidden_snippets
    assert len(RECALL_SCENARIOS) == len(NEGATIVE_SCENARIOS) == 4
    assert all(s.get("forbidden_snippets") for s in NEGATIVE_SCENARIOS)
    assert len(NEGATIVE_QUERIES) >= 5
