"""eval ingest 与 id_map（无 Qdrant）。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from evals.case.rag.ingest import FIXTURE_VERSION, build_id_map, pick_relevant_by_keywords
from evals.case.shared.assertions import score_rag_channel
from agent.case_generate.rag import CHANNEL_HISTORICAL_REQUIREMENT, CHANNEL_HISTORICAL_TEST_CASES

CASE_ROOT = Path(__file__).resolve().parents[1] / "evals" / "case"
RAG_DIR = CASE_ROOT / "rag"
ID_MAP_PATH = RAG_DIR / "id_map.json"
RAG_YAML = RAG_DIR / "promptfooconfig.yaml"


def test_map_only_produces_stable_login_chunk_ids():
    id_map = build_id_map(upload=False, reset=False)
    login_ex = next(
        e for e in id_map["requirements"]
        if e["file_name"] == "prd_001.md"
        and "3.3 异常与文案" in str(e.get("header_path") or "")
    )
    assert login_ex["point_id"] == "52b11505-2605-54d8-b49a-49ab544d3a9c"


def test_id_map_file_matches_fixture_version():
    data = json.loads(ID_MAP_PATH.read_text(encoding="utf-8"))
    assert data["fixture_version"] == FIXTURE_VERSION
    assert len(data["requirements"]) > 0
    assert len(data["test_cases"]) >= 1


def test_rag_yaml_relevant_ids_exist_in_id_map():
    id_map = json.loads(ID_MAP_PATH.read_text(encoding="utf-8"))
    known = {e["point_id"] for e in id_map["requirements"] + id_map["test_cases"]}
    cfg = yaml.safe_load(RAG_YAML.read_text(encoding="utf-8"))
    test = (cfg.get("tests") or [])[0]
    rag = test["vars"]["rag_scene"]["expected_rag"]
    for channel in ("historical_requirements", "historical_test_cases"):
        ids = rag[channel]["relevant_ids"]
        assert ids, f"{channel} relevant_ids 不应为空"
        assert all(rid in known for rid in ids)


def test_pick_relevant_by_keywords_login_hist():
    id_map = json.loads(ID_MAP_PATH.read_text(encoding="utf-8"))
    hits = pick_relevant_by_keywords(
        id_map["test_cases"],
        file_name="tc_login_hist_001.md",
        keywords=["用户名密码错误", "验证码过期"],
        limit=5,
    )
    assert "03f52dee-412d-5ee8-b5bd-8863494da0c5" in hits
    assert "ff1ccc5b-4d10-5e74-9826-0b828da5e284" in hits


def test_rag_gold_ids_align_with_yaml_trace_mock():
    cfg = yaml.safe_load(RAG_YAML.read_text(encoding="utf-8"))
    scene = cfg["tests"][0]["vars"]["rag_scene"]
    trace = {
        "用户登录": {
            "channels": {
                CHANNEL_HISTORICAL_REQUIREMENT: {
                    "hit_ids": scene["expected_rag"]["historical_requirements"]["relevant_ids"],
                },
                CHANNEL_HISTORICAL_TEST_CASES: {
                    "hit_ids": scene["expected_rag"]["historical_test_cases"]["relevant_ids"],
                },
            },
        },
    }
    for channel in (CHANNEL_HISTORICAL_REQUIREMENT, CHANNEL_HISTORICAL_TEST_CASES):
        result = score_rag_channel({"retrieval_trace": trace}, scene, channel)
        assert result.get("skipped") is not True
        assert result["recall_at_k"] == 1.0
