from noesis.prompts.common_qa import build_common_qa_prompt
from noesis.prompts.super_agent import build_super_agent_prompt


def test_kb_prompt_requests_markdown_references_in_normal_answer() -> None:
    prompt = build_common_qa_prompt(kb_enabled=True)

    assert "网页和知识库来源统一使用简短编号 `[1]`" in prompt
    assert "[1] 来源标题 — 原始 URL" in prompt
    assert "[1] 文件名 — Collection: Collection 名" in prompt
    assert "### 参考资料" in prompt
    assert "必须逐字复制工具结果提供的值" in prompt
    assert "不得改写、翻译、省略扩展名" in prompt
    assert "直接输出正常 Markdown 回答" in prompt
    assert "不要把最终回答包装成 JSON" in prompt
    assert "cited_evidence_ids" not in prompt


def test_prompt_without_retrieval_does_not_add_citation_protocol() -> None:
    prompt = build_common_qa_prompt(kb_enabled=False, web_enabled=False)

    assert "### 参考资料" not in prompt


def test_web_prompt_requests_inline_markdown_links() -> None:
    prompt = build_common_qa_prompt(kb_enabled=False, web_enabled=True)

    assert "[1] 来源标题 — 原始 URL" in prompt
    assert "不得编造来源" in prompt


def test_super_agent_uses_same_prompt_citation_contract() -> None:
    prompt = build_super_agent_prompt()

    assert "网页和知识库来源统一使用简短编号 `[1]`" in prompt
    assert "### 参考资料" in prompt
    assert "不要调用用于提交答案的虚拟工具" in prompt
