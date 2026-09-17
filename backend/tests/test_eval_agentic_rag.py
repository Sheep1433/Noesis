import json

import pytest

from evals.agent.rag.__main__ import load_dataset
from evals.agent.rag.runner import run_agentic_rag_sample
from evals.agent.rag.to_erb import collected_files, records_to_erb


def test_to_erb_maps_kb_files_to_dsid():
    name_to_dsid = {"a.md": "dsid_" + "0" * 32, "b.md": "dsid_" + "1" * 32}
    records = [
        {
            "question_id": "qst_0001",
            "completed": True,
            "final_text": "回答",
            "tool_outputs": [
                {
                    "name": "search_knowledge_base",
                    "output": json.dumps(
                        {"results": [{"file_name": "a.md"}, {"file_name": "a.md"},
                                     {"file_name": "unknown.md"}]},
                        ensure_ascii=False,
                    ),
                },
                {"name": "web_search", "output": '{"file_name":"ignored.md"}'},
            ],
        },
        {"question_id": "qst_0002", "completed": False, "final_text": "", "tool_outputs": []},
    ]
    erb_records, unmapped = records_to_erb(records, name_to_dsid)
    assert len(erb_records) == 1  # 未完成的题不导出
    assert erb_records[0]["question_id"] == "qst_0001"
    assert erb_records[0]["document_ids"] == ["dsid_" + "0" * 32]  # 去重；无映射不计入
    assert unmapped == {"unknown.md"}


def test_to_erb_collects_get_knowledge_document():
    outputs = [
        {"name": "get_knowledge_document",
         "output": json.dumps({"file_name": "doc.md"}, ensure_ascii=False)},
    ]
    assert collected_files(outputs) == ["doc.md"]


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
async def test_agentic_rag_runner_drives_cli_agent(monkeypatch):
    calls: dict = {}

    async def fake_run_cli_agent(**kwargs):
        calls.update(kwargs)
        return {
            "completed": True,
            "error": None,
            "final_text": "回答全文",
            "tool_stats": {"search_knowledge_base": 1},
            "tool_outputs": [
                {"name": "search_knowledge_base", "input": {},
                 "output": json.dumps({"results": [{"file_name": "guide.md"}]},
                                      ensure_ascii=False)},
            ],
            "session_usage": {"input_tokens": 1200, "output_tokens": 300},
            "latency_ms": 1500,
        }

    monkeypatch.setattr("evals.agent.rag.runner.run_cli_agent", fake_run_cli_agent)
    result = await run_agentic_rag_sample(
        {"id": "one", "query": "问题", "collection_names": ["kb"]},
        eval_user="test",
    )

    # CLI 契约：common 场景限定 KB 集合、关闭联网、落在评测账号
    assert calls["query"] == "问题"
    assert calls["qa_type"] == "common"
    assert calls["kb_collections"] == ["kb"]
    assert calls["web_search"] is False
    assert calls["user_id"] == "test"
    assert calls["session_id"].startswith("eval-agentic-rag-one-")

    # 记录映射：token 取自 session_usage，工具轨迹保留供 to_erb 导出
    assert result["completed"] is True
    assert result["input_tokens"] == 1200
    assert result["output_tokens"] == 300
    assert result["tool_outputs"][0]["name"] == "search_knowledge_base"
    assert result["session_id"] == calls["session_id"]
