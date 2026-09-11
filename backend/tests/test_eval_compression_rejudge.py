"""压缩重判模式测试：复用作答原文、仅换 judge 重打分（fake LLM，零外部依赖）。"""

import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def source_dir(tmp_path):
    src = tmp_path / "src-tag"
    (src / "runs").mkdir(parents=True)
    (src / "manifest.json").write_text(json.dumps({
        "schema_version": "noesis-eval-manifest/v1",
        "eval_line": "compression", "tag": "src-tag",
        "models": {"subject": "glm-subject", "judge": "old-judge"},
    }, ensure_ascii=False), encoding="utf-8")
    probes = [
        {"probe_id": "p1", "type": "recall", "question": "q1",
         "reference_answer": "a1", "continuation_text": "答案一"},
        {"probe_id": "p2", "type": "recall", "question": "q2",
         "reference_answer": "a2", "continuation_text": "答案二"},
    ]
    (src / "runs" / "f1.compressed:current.r0.json").write_text(json.dumps({
        "fixture_id": "f1", "arm": "compressed:current", "run_index": 0,
        "compression": {"pre_tokens": 1000, "post_tokens": 200, "compression_ratio": 0.8},
        "probes": probes,
    }, ensure_ascii=False), encoding="utf-8")
    return src


class FakeJudge:
    def __init__(self, verdict=2):
        self.verdict = verdict

    def invoke(self, _prompt):
        class R:
            content = json.dumps({
                "recall": self.verdict, "accuracy": 4, "artifact_trail": 4,
                "context_awareness": 4, "continuity": 4, "completeness": 4, "notes": ""})
        return R()


def _args(tmp_path, src, tag, judge="new-judge"):
    return SimpleNamespace(
        rejudge_from=src, tag=tag, judge_model_id=judge,
        judge_model_user="", model_user="", compare_to=None)


def _run_rejudge(tmp_path, monkeypatch, args):
    import evals.compression.__main__ as main_mod
    import evals.compression.report as report
    monkeypatch.setattr(main_mod, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(report, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr("noesis.llm.get_llm", lambda model_id=None: FakeJudge(2))
    return main_mod.rejudge(args)


def test_rejudge_reuses_answers_and_writes_new_tag(tmp_path, monkeypatch, source_dir):
    rc = _run_rejudge(tmp_path, monkeypatch, _args(tmp_path, source_dir, "rj1"))
    assert rc == 0
    out = tmp_path / "results" / "rj1"
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    # 2 题全 recall=2 → recall% = 100%
    assert summary["arms"]["compressed:current"]["recall_pct"] == 1.0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["config"]["rejudge"] is True
    assert manifest["models"]["subject"] == "glm-subject"
    assert manifest["models"]["judge"] == "new-judge"
    # 源产物不被改写
    src_probe = json.loads(
        (source_dir / "runs" / "f1.compressed:current.r0.json").read_text(encoding="utf-8"))
    assert src_probe["probes"][0].get("recall") is None


def test_rejudge_rejects_same_judge_as_subject(tmp_path, monkeypatch, source_dir):
    import evals.compression.__main__ as main_mod
    with pytest.raises(ValueError, match="不得与被测模型相同"):
        _run_rejudge(tmp_path, monkeypatch, _args(tmp_path, source_dir, "rj2", judge="glm-subject"))


def test_rejudge_requires_source_manifest(tmp_path, monkeypatch):
    import evals.compression.__main__ as main_mod
    empty = tmp_path / "empty-tag"
    empty.mkdir()
    rc = _run_rejudge(tmp_path, monkeypatch, _args(tmp_path, empty, "rj3"))
    assert rc == 2


def test_rejudge_aggregates_runs_per_fixture_arm(tmp_path, monkeypatch, source_dir):
    """同 fixture×arm 的多个 run 聚合为一行（runs=N），与首跑汇总同口径：
    逐 run 各出一行会让逐 fixture 表重复，且 delta 计算同 key 后行覆盖前行。"""
    second = json.loads(
        (source_dir / "runs" / "f1.compressed:current.r0.json").read_text(encoding="utf-8"))
    second["run_index"] = 1
    (source_dir / "runs" / "f1.compressed:current.r1.json").write_text(
        json.dumps(second, ensure_ascii=False), encoding="utf-8")
    rc = _run_rejudge(tmp_path, monkeypatch, _args(tmp_path, source_dir, "rj4"))
    assert rc == 0
    summary = json.loads(
        (tmp_path / "results" / "rj4" / "summary.json").read_text(encoding="utf-8"))
    rows = [r for r in summary["fixtures"] if r["fixture_id"] == "f1"]
    assert len(rows) == 1
    assert rows[0]["runs"] == 2
    assert summary["runs_per_arm"] == 2
