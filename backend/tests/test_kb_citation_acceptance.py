"""Fixed requirement_docs citation acceptance at the provider-independent protocol boundary."""

from noesis_server.domain.chat.message_builder import AssistantMessageBuilder


def _result(index: int, excerpt: str) -> dict:
    return {
        "collection_name": "requirement_docs",
        "document_id": f"doc_{index}",
        "document_version_id": f"docv_{index}",
        "segment_id": f"seg_{index}",
        "file_name": f"需求-{index}.md",
        "excerpt": excerpt,
        "citable": True,
        "evidence_id": f"ev_{index}",
        "tool_call_ids": ["call_search_1"],
    }


def test_requirement_docs_cited_and_retrieved_layers_refresh_without_markers() -> None:
    builder = AssistantMessageBuilder(session_id="s1", message_id="m1")
    retrieval = builder.register_retrieval_results(
        tool_call_id="call_search_1",
        query="验证码有效期多久",
        results=[
            _result(1, "验证码发送后五分钟内有效。"),
            _result(2, "验证码每天最多发送十次。"),
            _result(3, "登录失败会记录审计日志。"),
        ],
    )
    answer = builder.apply_typed_segments([
        {"text": "验证码有效期为五分钟。", "cited_evidence_ids": ["ev_1"]},
    ])

    assert len(retrieval.results) == 3
    assert [item["evidence_id"] for item in answer.annotations] == ["ev_1"]
    assert "[[source:" not in answer.content
    assert "[ID:" not in answer.content
    assert "ev_1" not in answer.content

    restored = AssistantMessageBuilder(session_id="s1", message_id="m1")
    restored.load_from_content_dict(builder.to_dict())
    snapshot = restored.to_dict()
    assert len(next(part for part in snapshot["parts"] if part["type"] == "retrieval")["results"]) == 3
    assert next(part for part in snapshot["parts"] if part["type"] == "text")["annotations"][0]["evidence_id"] == "ev_1"


def test_requirement_docs_provider_fallback_has_retrieval_but_no_inferred_citation() -> None:
    builder = AssistantMessageBuilder(session_id="s2", message_id="m2")
    builder.register_retrieval_results(
        tool_call_id="call_search_1",
        query="验证码有效期多久",
        results=[_result(1, "验证码发送后五分钟内有效。")],
    )
    text = builder.append_text("验证码有效期为五分钟。")
    assert text.annotations == []
    assert any(part["type"] == "retrieval" for part in builder.to_dict()["parts"])
