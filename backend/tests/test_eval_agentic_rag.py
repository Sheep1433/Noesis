import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from evals.agent.rag.__main__ import load_dataset
from evals.agent.rag.scoring import retrieved_sources, score_expected_sources
from evals.agent.rag.runner import run_agentic_rag_sample


def test_agentic_rag_source_scoring_uses_kb_tool_outputs():
    outputs = [
        {
            "name": "search_knowledge_base",
            "output": json.dumps(
                {"results": [{"file_name": "a.md"}, {"file_name": "b.md"}]},
                ensure_ascii=False,
            ),
        },
        {"name": "web_search", "output": '{"file_name":"ignored.md"}'},
    ]
    assert retrieved_sources(outputs) == {"a.md", "b.md"}
    score = score_expected_sources(outputs, ["a.md", "missing.md"])
    assert score["matched_sources"] == ["a.md"]
    assert score["source_recall"] == 0.5


def test_agentic_rag_dataset_requires_query(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text('{"id":"bad"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing query"):
        load_dataset(dataset)


def test_agentic_rag_dataset_loads_scope_and_sources(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"id":"one","query":"q","collection_names":["kb"],"expected_sources":["a.md"]}\n',
        encoding="utf-8",
    )
    assert load_dataset(dataset)[0]["collection_names"] == ["kb"]


@pytest.mark.asyncio
async def test_agentic_rag_runner_uses_general_qa_harness_profile(monkeypatch):
    calls = []

    class FakeAgent:
        async def run_agent(self, query, **kwargs):
            calls.append((query, kwargs))
            yield {"event": "on_tool_start", "name": "search_knowledge_base", "run_id": "1"}
            yield {
                "event": "on_tool_end",
                "name": "search_knowledge_base",
                "run_id": "1",
                "data": {
                    "output": SimpleNamespace(
                        content=json.dumps({"results": [{"file_name": "guide.md"}]})
                    )
                },
            }
            yield {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="回答")}}
            yield {"type": "__tw_finish__", "finish_reason": "stop"}

        async def cancel_task(self, _session_id):
            return True

    @asynccontextmanager
    async def fake_runtime(**_kwargs):
        yield

    monkeypatch.setattr("evals.agent.rag.runner.GeneralQAAgent", FakeAgent)
    monkeypatch.setattr("evals.agent.rag.runner.eval_runtime", fake_runtime)
    result = await run_agentic_rag_sample(
        {
            "id": "one",
            "query": "问题",
            "collection_names": ["kb"],
            "expected_sources": ["guide.md"],
        }
    )

    assert result["completed"] is True
    assert result["kb_tool_called"] is True
    assert result["source_score"]["source_recall"] == 1.0
    assert calls[0][1]["kb_collections"] == ["kb"]
    assert calls[0][1]["web_search_enabled"] is False
