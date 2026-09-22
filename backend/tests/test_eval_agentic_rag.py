import json

import pytest

import evals.agent.rag.runner as runner_mod
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


class _FakeStream:
    """SSE 流桩：aiter_lines 产出 event/data 行。"""

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttp:
    """httpx 客户端桩：覆盖 runner 用到的三个调用。"""

    def __init__(self, messages):
        self.created_session = None
        self.created_run = None
        self._messages = messages

    async def post(self, url, json=None, data=None, headers=None):
        if url == "/api/chat/sessions":
            self.created_session = json
            return _resp(200, {"code": 200, "data": {"id": "eval-agentic-rag-x"}})
        if url == "/api/chat/runs":
            self.created_run = json
            return _resp(200, {"code": 200, "data": {"run_id": "run-1"}})
        raise AssertionError(f"unexpected post {url}")

    async def get(self, url):
        if url.endswith("/messages"):
            return _resp(200, {"code": 200, "data": self._messages})
        raise AssertionError(f"unexpected get {url}")

    def stream(self, method, url, params=None):
        lines = [
            "event: run-started",
            'data: {"type": "run-started"}',
            "",
            "event: tool-output-available",
            'data: {"type": "tool-output-available", "tool_call_id": "t1", '
            '"name": "search_knowledge_base", "output": "{}"}',
            "",
            "event: run-completed",
            'data: {"type": "run-completed", "usage": {"input_tokens": 99, "output_tokens": 7}}',
            "",
        ]
        return _FakeStream(lines)


class _Resp:
    def __init__(self, code, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


def _resp(code, body):
    return _Resp(code, body)


@pytest.mark.asyncio
async def test_agentic_rag_runner_drives_production_http(monkeypatch):
    messages = [
        {"role": "user", "content": {"parts": [{"type": "text", "content": "问题"}]}},
        {"role": "assistant", "content": {"parts": [
            {"type": "text", "content": "回答全文"},
            {"type": "tool", "name": "search_knowledge_base",
             "input": {}, "output": json.dumps(
                 {"results": [{"file_name": "guide.md"}]}, ensure_ascii=False)},
        ]},
         "extra": {"usage": {"input_tokens": 99, "output_tokens": 7}}},
    ]
    fake = _FakeHttp(messages)

    async def fake_get_http():
        return fake

    monkeypatch.setattr(runner_mod, "_get_http", fake_get_http)
    result = await run_agentic_rag_sample(
        {"id": "one", "query": "问题", "collection_names": ["kb"]},
        model_id="glm-5.3-flash",
        eval_user="test",
    )

    # 生产契约：common run 携带 KB 限定与关联网标记
    assert fake.created_run["extra"]["qa_type"] == "COMMON_QA"
    assert fake.created_run["extra"]["kb_collections"] == ["kb"]
    assert fake.created_run["extra"]["kb_search_enabled"] is True

    # 终值读 DB 权威 assistant 消息；工具轨迹从消息 parts 重建供 to_erb 导出
    assert result["completed"] is True
    assert result["final_text"] == "回答全文"
    assert result["input_tokens"] == 99
    assert result["tool_outputs"][0]["name"] == "search_knowledge_base"
    assert "guide.md" in result["tool_outputs"][0]["output"]
