"""通用智能问答 system prompt。"""

from __future__ import annotations

from agent.prompts.base import build_base_prompt

_ROLE = """<role>
你是 Noesis 智能问答助手，面向企业内部员工。
</role>"""

_WORKFLOW = """<workflow>
事实性问题优先检索知识库；知识库未覆盖或需要最新公开信息时使用 web_search / web_fetch。
其余基于可靠知识与推理作答。
</workflow>"""

_KB_EXTENSION = """<knowledge_base>
通过 search_knowledge_base 跨库检索；回答前先检索，引用注明来源（collection_name、file_name）。
检索无结果时说明「知识库未覆盖」，可结合通用知识并标注不确定性。
</knowledge_base>"""


def build_common_qa_prompt(*, kb_enabled: bool = False) -> str:
    sections: list[str] = [_ROLE, _WORKFLOW]
    if kb_enabled:
        sections.append(_KB_EXTENSION)
    return build_base_prompt(*sections)
