from noesis.prompts.common_qa import build_common_qa_prompt
from types import SimpleNamespace

import noesis.agents.common_qa as common_qa


def test_kb_prompt_requests_typed_binding_without_body_markers() -> None:
    prompt = build_common_qa_prompt(kb_enabled=True)
    assert "segments[{text,cited_evidence_ids}]" in prompt
    assert "无依据时使用空数组" in prompt
    assert "正文不得输出 evidence_id" in prompt
    assert "[[source:...]]" in prompt


def test_prompt_without_retrieval_does_not_add_citation_protocol() -> None:
    prompt = build_common_qa_prompt(kb_enabled=False, web_enabled=False)
    assert "cited_evidence_ids" not in prompt


def test_web_only_prompt_requests_typed_binding() -> None:
    prompt = build_common_qa_prompt(kb_enabled=False, web_enabled=True)
    assert "segments[{text,cited_evidence_ids}]" in prompt
    assert "web_search" in prompt


def test_structured_citations_are_gated_by_verified_model(monkeypatch) -> None:
    monkeypatch.setattr(common_qa, "ModelConfig", SimpleNamespace(
        structured_citations_enabled=True,
        structured_citation_models=("mimo-v2.5-free",),
        model_name="mimo-v2.5-free",
    ))
    monkeypatch.setattr(common_qa, "resolve_catalog_entry", lambda model_id: SimpleNamespace(
        model_name={"mimo": "mimo-v2.5-free", "flash": "deepseek-v4-flash-free"}[model_id]
    ))
    assert common_qa._structured_citations_enabled(None) is True
    assert common_qa._structured_citations_enabled("mimo") is True
    assert common_qa._structured_citations_enabled("flash") is False
