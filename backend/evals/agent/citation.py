"""引用溯源评测：格式遵循率与引用正确率（确定性）+ 事实可溯源率（LLM judge）。

引用契约（agents/prompts/citations.py）：``[citation:文件名](kb:Collection/文件名)``，
网页来源为 ``[citation:标题](URL)``。已知失败模式：``file:`` 等伪协议头、
中文文件名被 URL 编码（%XX）、引用文档不在 GT 集合（张冠李戴）。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Iterable, Protocol

CITATION_RE = re.compile(r"\[citation:([^\]]+)\]\(([^)]+)\)")

# 引用 ref 的合法形态：知识库 kb: 前缀，或 http(s) URL
_KB_REF_RE = re.compile(r"^kb:[^/]+/.+$")
_URL_REF_RE = re.compile(r"^https?://\S+$")


class SupportsInvoke(Protocol):
    def invoke(self, prompt: Any) -> Any: ...


def parse_citations(answer: str) -> list[dict[str, str]]:
    """解析回答中的全部引用，返回 [{label, ref}]；label 为文件名/标题，ref 为引用目标。"""
    return [{"label": m.group(1).strip(), "ref": m.group(2).strip()}
            for m in CITATION_RE.finditer(answer or "")]


def _ref_failure_modes(citations: list[dict[str, str]]) -> list[str]:
    """已知确定性失败模式（回归断言用）。"""
    failures: list[str] = []
    for c in citations:
        ref = c["ref"]
        if not (_KB_REF_RE.match(ref) or _URL_REF_RE.match(ref)):
            failures.append(f"伪协议头或畸形 ref: {ref[:80]}")
        elif _KB_REF_RE.match(ref) and "%" in ref:
            failures.append(f"文件名被 URL 编码: {ref[:80]}")
    return failures


def citation_metrics(
    answer: str,
    *,
    expected_doc_files: Iterable[str],
) -> dict[str, Any]:
    """确定性两指标：格式遵循率（存在至少一个契约形态的引用）与引用正确率（引用 ∈ GT）。

    expected_doc_files 为该题 GT 的文件名集合（与 citation label / kb ref 尾段对齐）。
    """
    citations = parse_citations(answer)
    expected = {str(f).strip() for f in expected_doc_files if str(f).strip()}
    well_formed = [c for c in citations
                   if _KB_REF_RE.match(c["ref"]) or _URL_REF_RE.match(c["ref"])]
    # 引用正确性只对知识库引用评（web URL 无法对 GT）
    kb_cited: set[str] = set()
    for c in well_formed:
        if _KB_REF_RE.match(c["ref"]):
            kb_cited.add(c["ref"].split("/", 1)[1].strip())
            kb_cited.add(urllib.parse.unquote(c["ref"].split("/", 1)[1]).strip())
    correct = kb_cited & expected
    return {
        "citation_count": len(citations),
        "well_formed_count": len(well_formed),
        "format_compliant": bool(well_formed) and not _ref_failure_modes(citations),
        "failure_modes": _ref_failure_modes(citations),
        "cited_kb_files": sorted(kb_cited),
        "expected_files": sorted(expected),
        "correct_citations": sorted(correct),
        # 引用正确率 = 正确引用数 / 全部知识库引用数（无知识库引用时无定义）
        "citation_accuracy": len(correct) / len(kb_cited) if kb_cited else None,
    }


def _collect_excerpts(tool_outputs: Iterable[dict[str, Any]]) -> dict[str, str]:
    """file_name → 检索片段正文（事实溯源的 evidence 池）。"""
    excerpts: dict[str, str] = {}
    for item in tool_outputs:
        if item.get("name") != "search_knowledge_base":
            continue
        raw = item.get("output")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for hit in payload.get("results") or []:
            if isinstance(hit, dict) and hit.get("file_name"):
                name = str(hit["file_name"]).strip()
                excerpts.setdefault(name, str(hit.get("excerpt") or ""))
    return excerpts


def build_fact_grounding_prompt(
    *, answer_facts: list[str], evidence: dict[str, str]
) -> str:
    evidence_block = "\n\n".join(
        f"### {name}\n{text[:4000]}" for name, text in evidence.items()
    ) or "（无检索片段）"
    facts_block = "\n".join(f"{i}. {f}" for i, f in enumerate(answer_facts, 1))
    return f"""你是引用溯源评测裁判。判断每条事实能否在给定的检索片段中找到支撑。

判定标准：
- supported：片段中存在与该事实一致的信息（数值、名称、结论可直接对上）。
- unsupported：片段无法支撑该事实，或只有主题相关但细节对不上。

ANSWER FACTS（待判定事实）:
{facts_block}

EVIDENCE（检索片段）:
{evidence_block}

仅输出 JSON 数组，不要其它文字：
[{{"fact_index": 1, "supported": true, "notes": "可空"}}]"""


def parse_fact_grounding_response(
    raw: str, *, n_facts: int
) -> list[dict[str, Any]]:
    if not raw or not raw.strip():
        raise ValueError("empty judge response")
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    arr = re.search(r"\[[\s\S]*\]", text)
    if not arr:
        raise ValueError(f"no JSON array in judge response: {raw[:200]!r}")
    parsed = json.loads(arr.group(0))
    if not isinstance(parsed, list) or len(parsed) != n_facts:
        raise ValueError(f"expect {n_facts} entries, got {len(parsed)}")
    out = []
    for i, entry in enumerate(parsed, 1):
        if not isinstance(entry, dict) or int(entry.get("fact_index") or i) != i:
            raise ValueError(f"fact_index mismatch at {i}")
        out.append({
            "fact_index": i,
            "supported": bool(entry.get("supported")),
            "notes": str(entry.get("notes") or "")[:200],
        })
    return out


def judge_fact_grounding(
    *,
    answer_facts: list[str],
    tool_outputs: Iterable[dict[str, Any]],
    cited_files: Iterable[str],
    llm: SupportsInvoke,
) -> dict[str, Any]:
    """事实可溯源率：逐条 answer_fact 判「引用的检索片段是否支撑」。

    evidence 只取被引用文档的片段（张冠李戴的引用不该替事实撑腰）；
    解析失败重试一次，仍失败标记 invalid。
    """
    excerpts = _collect_excerpts(tool_outputs)
    evidence = {f: excerpts[f] for f in cited_files if f in excerpts}
    if not answer_facts:
        return {"facts": [], "grounding_rate": None, "parse_error": None}
    prompt = build_fact_grounding_prompt(
        answer_facts=answer_facts, evidence=evidence)
    raw = ""
    for _attempt in range(2):
        raw = str(llm.invoke(prompt).content or "")
        try:
            facts = parse_fact_grounding_response(raw, n_facts=len(answer_facts))
            supported = sum(1 for f in facts if f["supported"])
            return {
                "facts": facts,
                "grounding_rate": supported / len(facts),
                "parse_error": None,
            }
        except ValueError:
            continue
    return {"facts": [], "grounding_rate": None,
            "parse_error": "fact grounding judge unparseable after retry"}
