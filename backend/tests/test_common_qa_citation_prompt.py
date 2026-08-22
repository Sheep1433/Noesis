from noesis.agents.prompts.common_qa import build_common_qa_prompt
from noesis.agents.prompts.super_agent import build_super_agent_prompt


def test_kb_prompt_requests_inline_citation_links() -> None:
    prompt = build_common_qa_prompt(kb_enabled=True)

    assert "[citation:来源标题](原始URL)" in prompt
    assert "逐字复制工具结果的 citation_ref 字段" in prompt
    assert "kb:Collection名/文件名" in prompt
    assert "必须逐字复制工具结果提供的值" in prompt
    assert "不要在回答末尾添加「### 参考资料」章节" in prompt
    assert "cited_evidence_ids" not in prompt


def test_prompt_without_retrieval_does_not_add_citation_protocol() -> None:
    prompt = build_common_qa_prompt(kb_enabled=False, web_enabled=False)

    assert "<citations>" not in prompt


def test_web_prompt_requests_inline_markdown_links() -> None:
    prompt = build_common_qa_prompt(kb_enabled=False, web_enabled=True)

    assert "[citation:来源标题](原始URL)" in prompt
    assert "不得改写或编造" in prompt


def test_super_agent_uses_same_prompt_citation_contract() -> None:
    prompt = build_super_agent_prompt()

    assert "[citation:来源标题](原始URL)" in prompt
    assert "逐字复制工具结果的 citation_ref 字段" in prompt
