"""rag scorer（mock retrieval_trace，无 Qdrant）。"""

from agent.case_generate.rag import (
    CHANNEL_CURRENT_REQUIREMENT,
    CHANNEL_HISTORICAL_REQUIREMENT,
    CHANNEL_HISTORICAL_TEST_CASES,
)
from evals.scorers.rag_hit import score_rag


def _trace_hit():
    return {
        "用户登录": {
            "scene_name": "用户登录",
            "channels": {
                CHANNEL_CURRENT_REQUIREMENT: {"hit_ids": ["req-chunk-1", "req-chunk-2"]},
                CHANNEL_HISTORICAL_REQUIREMENT: {"hit_ids": ["req-hist-1"]},
                CHANNEL_HISTORICAL_TEST_CASES: {"hit_ids": ["tc-ref-9"]},
            },
        }
    }


def test_rag_hit_at_3_all_hit():
    gt = {
        "expected_rag": {
            "用户登录": {
                CHANNEL_CURRENT_REQUIREMENT: {"expected_ids": ["req-chunk-1"]},
                CHANNEL_HISTORICAL_TEST_CASES: {"expected_ids": ["tc-ref-9"]},
            }
        }
    }
    result = score_rag({"state": {"retrieval_trace": _trace_hit()}}, gt)
    assert result["rag_hit_at_3"] == 1.0
    assert result["rag_eval_incomplete"] is False


def test_rag_hit_partial():
    gt = {
        "expected_rag": {
            "用户登录": {
                CHANNEL_CURRENT_REQUIREMENT: {"expected_ids": ["missing-id"]},
                CHANNEL_HISTORICAL_TEST_CASES: {"expected_ids": ["tc-ref-9"]},
            }
        }
    }
    result = score_rag({"state": {"retrieval_trace": _trace_hit()}}, gt)
    assert result["rag_hit_at_3"] == 0.5


def test_rag_skipped_no_expected():
    result = score_rag({"state": {"retrieval_trace": _trace_hit()}}, {})
    assert result["skipped"] is True


def test_rag_incomplete_empty_trace():
    gt = {
        "expected_rag": {
            "用户登录": {
                CHANNEL_CURRENT_REQUIREMENT: {"expected_ids": ["req-chunk-1"]},
            }
        }
    }
    result = score_rag({"state": {"retrieval_trace": {}}}, gt)
    assert result["rag_eval_incomplete"] is True
