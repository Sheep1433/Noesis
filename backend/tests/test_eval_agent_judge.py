"""Agent E2E 判卷与断点续跑测试（fake LLM，不触外部服务）。"""

import json

import pytest

from evals.agent.rag.__main__ import _is_error, append_raw_record, load_raw_records
from evals.agent.rag.judge import (
    build_judge_prompt,
    judge_answer,
    parse_judge_response,
)


class FakeLLM:
    def __init__(self, replies):
        self._replies = list(replies)

    def invoke(self, _prompt):
        class R:
            content = self._replies.pop(0)
        return R()


def test_parse_judge_response_plain_and_fenced():
    assert parse_judge_response('{"verdict": "accepted", "notes": "ok"}')["verdict"] == "accepted"
    parsed = parse_judge_response('```json\n{"verdict": "partial"}\n```')
    assert parsed["verdict"] == "partial"


def test_parse_judge_response_rejects_bad_verdict():
    with pytest.raises(ValueError, match="invalid verdict"):
        parse_judge_response('{"verdict": "excellent"}')


def test_judge_answer_retries_once_on_bad_output():
    llm = FakeLLM(["not json", '{"verdict": "accepted"}'])
    out = judge_answer(question="q", gold_answer="g", answer="a", llm=llm)
    assert out["verdict"] == "accepted"
    assert out["parse_error"] is None


def test_judge_answer_marks_invalid_after_retry():
    llm = FakeLLM(["garbage", "still garbage"])
    out = judge_answer(question="q", gold_answer="g", answer="a", llm=llm)
    assert out["verdict"] == "invalid"
    assert out["parse_error"]


def test_build_judge_prompt_contains_anchors():
    prompt = build_judge_prompt(question="Q", gold_answer="G", answer="A")
    assert "GOLD ANSWER" in prompt and "ASSISTANT ANSWER" in prompt


def test_raw_records_last_write_wins(tmp_path):
    raw = tmp_path / "raw.jsonl"
    append_raw_record(raw, {"sample_id": "s1", "completed": True, "error": None})
    append_raw_record(raw, {"sample_id": "s1", "completed": False, "error": "timeout"})
    append_raw_record(raw, {"sample_id": "s2", "completed": True, "error": None})
    records = load_raw_records(raw)
    assert records["s1"]["error"] == "timeout"
    assert _is_error(records["s1"]) is True
    assert _is_error(records["s2"]) is False


def test_load_raw_records_missing_file(tmp_path):
    assert load_raw_records(tmp_path / "nope.jsonl") == {}


def test_raw_records_roundtrip_serializable(tmp_path):
    raw = tmp_path / "raw.jsonl"
    record = {"sample_id": "s", "citation": {"format_compliant": True}}
    append_raw_record(raw, record)
    assert load_raw_records(raw)["s"] == json.loads(json.dumps(record))
