"""阶段 B RAG Recall@K scorer（mock retrieval_trace，无 Qdrant）。"""

from agent.case_generate.rag import (
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)
from evals.case.shared.assertions import score_stage_b_channel


def _trace_hit():
    return {
        "用户登录": {
            "scene_name": "用户登录",
            "channels": {
                CHANNEL_HISTORICAL_REQUIREMENT: {"hit_ids": ["req-hist-1", "req-hist-2"]},
                CHANNEL_HISTORICAL_TEST_CASES: {"hit_ids": ["tc-ref-9"]},
            },
        }
    }


def _scene_cfg(**rag):
    return {
        "scene_name": "用户登录",
        "expected_rag": rag,
    }


def test_recall_at_3_all_hit():
    scene = _scene_cfg(
        **{
            CHANNEL_HISTORICAL_TEST_CASES: {"relevant_ids": ["tc-ref-9"], "k": 3},
        }
    )
    result = score_stage_b_channel(
        {"retrieval_trace": _trace_hit()},
        scene,
        CHANNEL_HISTORICAL_TEST_CASES,
    )
    assert result["recall_at_k"] == 1.0
    assert result["hit_at_k"] == 1.0


def test_recall_at_3_partial():
    scene = _scene_cfg(
        **{
            CHANNEL_HISTORICAL_REQUIREMENT: {
                "relevant_ids": ["missing-id", "req-hist-1"],
                "k": 3,
            },
        }
    )
    result = score_stage_b_channel(
        {"retrieval_trace": _trace_hit()},
        scene,
        CHANNEL_HISTORICAL_REQUIREMENT,
    )
    assert result["recall_at_k"] == 0.5
    assert result["hit_at_k"] == 1.0


def test_channel_skipped_no_relevant_ids():
    scene = _scene_cfg(
        **{CHANNEL_HISTORICAL_TEST_CASES: {"relevant_ids": [], "k": 3}}
    )
    result = score_stage_b_channel(
        {"retrieval_trace": _trace_hit()},
        scene,
        CHANNEL_HISTORICAL_TEST_CASES,
    )
    assert result["skipped"] is True


def test_incomplete_empty_trace():
    scene = _scene_cfg(
        **{CHANNEL_HISTORICAL_REQUIREMENT: {"relevant_ids": ["req-hist-1"], "k": 3}}
    )
    result = score_stage_b_channel(
        {"retrieval_trace": {}},
        scene,
        CHANNEL_HISTORICAL_REQUIREMENT,
    )
    assert result["incomplete"] is True
