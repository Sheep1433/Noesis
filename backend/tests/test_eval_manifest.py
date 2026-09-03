"""统一评测基建（manifest/tag 防覆盖/judge 分离/抽检清单）测试，不依赖外部服务。"""

import json

import pytest

from evals.manifest import (
    TagExistsError,
    aggregate_usage,
    build_manifest,
    init_results_dir,
    require_judge_separation,
    write_manifest,
    write_manual_review_queue,
)


def test_require_judge_separation_rejects_same_model():
    with pytest.raises(ValueError, match="不得与被测模型相同"):
        require_judge_separation("glm-5.3-flash", "glm-5.3-flash")


def test_require_judge_separation_requires_explicit_judge():
    with pytest.raises(ValueError, match="judge"):
        require_judge_separation("glm-5.3-flash", None)


def test_require_judge_separation_accepts_distinct():
    require_judge_separation("glm-5.3-flash", "glm-5.3")


def test_init_results_dir_rejects_existing_tag(tmp_path):
    out = init_results_dir(tmp_path, "baseline")
    write_manifest(out, build_manifest(eval_line="kb", tag="baseline"))
    with pytest.raises(TagExistsError):
        init_results_dir(tmp_path, "baseline")
    # 新 tag 正常
    init_results_dir(tmp_path, "baseline-v2")
    # 续跑例外：同 tag 允许复用（只追加 raw，不改写历史）
    assert init_results_dir(tmp_path, "baseline", allow_resume=True) == out


def test_manifest_roundtrip(tmp_path):
    manifest = build_manifest(
        eval_line="agent-rag",
        tag="t1",
        subject_model="glm-5.3-flash",
        judge_model="glm-5.3",
        dataset={"path": "fixtures/x.jsonl", "count": 10, "seed": 11},
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    path = write_manifest(tmp_path, manifest)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["models"]["subject"] == "glm-5.3-flash"
    assert loaded["models"]["judge"] == "glm-5.3"
    assert loaded["usage"]["input_tokens"] == 100
    assert loaded["schema_version"].startswith("noesis-eval-manifest/")


def test_review_queue_deterministic_and_covers_fraction(tmp_path):
    records = [{"sample_id": str(i)} for i in range(40)]
    p1 = write_manual_review_queue(tmp_path, records, seed=7)
    p2 = write_manual_review_queue(tmp_path / "..", records, seed=7)
    q1 = json.loads(p1.read_text(encoding="utf-8"))
    q2 = json.loads(p2.read_text(encoding="utf-8"))
    assert len(q1["samples"]) == 4  # 10% of 40
    assert [s["sample_id"] for s in q1["samples"]] == [s["sample_id"] for s in q2["samples"]]
    assert q1["total"] == 40


def test_review_queue_small_dataset_at_least_one(tmp_path):
    records = [{"sample_id": "only"}]
    q = json.loads(write_manual_review_queue(tmp_path, records, seed=1).read_text(encoding="utf-8"))
    assert len(q["samples"]) == 1


def test_aggregate_usage_tolerates_missing_fields():
    usage = aggregate_usage([{"input_tokens": 5}, {"input_tokens": 3, "output_tokens": 2}, {}])
    assert usage == {"input_tokens": 8, "output_tokens": 2}
