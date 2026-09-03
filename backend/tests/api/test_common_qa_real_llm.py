"""COMMON_QA 真实知识库检索与引用接口测试。"""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.llm]


@pytest.mark.integration
def test_common_qa_kb_answer_uses_numbered_citation(
    auth_client,
    citation_kb_collection,
    create_session,
    create_run,
    collect_run_stream,
):
    """新入库文档产生可恢复的 KB retrieval 与编号引用。"""
    collection_name = citation_kb_collection
    session_id = create_session(
        title="api-test-common-qa-kb-citation",
        qa_type="COMMON_QA",
        extra={"kb_collections": [collection_name]},
    )
    run = create_run(
        session_id=session_id,
        content=(
            "请调用 search_knowledge_base 检索登录验证码规则，"
            "用一句话说明验证码有效期，并按要求给出知识库引用。"
        ),
        qa_type="COMMON_QA",
    )

    events = collect_run_stream(
        auth_client,
        run["run_id"],
        deadline_seconds=300,
    )
    kb_results = [
        result
        for name, payload in events.events
        if name == "retrieval-results-available" and isinstance(payload, dict)
        for result in payload.get("results") or []
        if isinstance(result, dict)
        and result.get("source_type") == "knowledge_base"
        and result.get("collection_name") == collection_name
    ]
    assert events.succeeded, f"SSE 未成功结束: {events.error}"
    assert "search_knowledge_base" in events.tool_names, (
        f"未调用 search_knowledge_base: {events.tool_names}"
    )
    assert kb_results, "实时流缺少临时 Collection 的 KB retrieval results"
    assert any(result.get("title") == "登录验证码规则.md" for result in kb_results)
    assert any("五分钟" in str(result.get("excerpt") or "") for result in kb_results)
    assert re.search(r"\[\d+\]", events.text), f"正文缺少编号引用: {events.text}"
    assert "### 参考资料" in events.text, f"正文缺少参考资料列表: {events.text}"
    assert "登录验证码规则.md" in events.text, f"参考资料缺少文件名: {events.text}"
    assert collection_name in events.text, f"参考资料缺少 Collection: {events.text}"

    response = auth_client.get(f"/api/chat/messages/{run['assistant_message_id']}")
    response.raise_for_status()
    parts = response.json()["data"]["content"]["parts"]
    assert any(part.get("type") == "retrieval" for part in parts), (
        "终态消息丢失 KB retrieval part"
    )
    text = "".join(
        part.get("content", "") for part in parts if part.get("type") == "text"
    )
    assert text == events.text, "KB 引用的流式正文与终态消息不一致"
