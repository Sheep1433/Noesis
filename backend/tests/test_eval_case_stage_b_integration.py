"""RAG 评测集成（默认 skip，需 Qdrant + embedding + ingest）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from evals.case.rag.provider import call_api
from evals.case.shared.assertions import (
    assert_document_context_present,
    assert_historical_requirements_hit_at_3,
    assert_historical_requirements_recall_at_3,
    assert_historical_test_cases_hit_at_3,
    assert_historical_test_cases_recall_at_3,
)

RAG_DIR = Path(__file__).resolve().parents[1] / "evals" / "case" / "rag"

pytestmark = pytest.mark.skipif(
    os.environ.get("NOESIS_CASE_STAGE_B_EVAL") != "1",
    reason="integration: set NOESIS_CASE_STAGE_B_EVAL=1 and run evals.case.rag.ingest first",
)


def _rag_case() -> dict:
    cfg = yaml.safe_load((RAG_DIR / "promptfooconfig.yaml").read_text(encoding="utf-8"))
    return (cfg.get("tests") or [])[0]


@pytest.mark.integration
def test_rag_login_retrieval_integration():
    case = _rag_case()
    context = {"vars": case["vars"]}
    response = call_api(case["vars"]["query"], context=context)
    output = response["output"]

    for fn in (
        assert_historical_requirements_recall_at_3,
        assert_historical_requirements_hit_at_3,
        assert_historical_test_cases_recall_at_3,
        assert_historical_test_cases_hit_at_3,
        assert_document_context_present,
    ):
        result = fn(output, context)
        assert result["pass"] is True, f"{fn.__name__}: {result.get('reason')}"

    payload = json.loads(output)
    assert payload.get("document_context_injected") is True
    assert payload.get("retrieval_trace")
