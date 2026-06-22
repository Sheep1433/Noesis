"""Probe continuation + Judge（均使用 get_llm()）。"""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from evals.compression.rubric import build_judge_prompt, parse_judge_response
from llm import get_llm

CONTINUATION_SYSTEM = (
    "你是长会话中的接续助手。较早轮次已被压缩进 handoff 摘要。"
    "请仅依据你看到的对话历史（含摘要）回答用户问题。"
    "不要臆造未出现的细节；若摘要缺少具体事实请明确说明。"
    "回答应直接、具体，尽量引用路径、配置项与错误信息。"
)


def _message_content(msg: AnyMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content or "")


def sanitize_for_continuation(messages: List[AnyMessage]) -> List[AnyMessage]:
    """剥离不完整 tool 配对，避免 strict provider 报错（对齐 hermes grader）。"""
    clean: List[AnyMessage] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            clean.append(
                HumanMessage(content=f"[earlier tool result: {msg.name or 'tool'}]\n{_message_content(msg)}")
            )
            continue
        if isinstance(msg, SystemMessage):
            continue
        if isinstance(msg, HumanMessage):
            clean.append(HumanMessage(content=_message_content(msg)))
        elif isinstance(msg, AIMessage):
            clean.append(AIMessage(content=_message_content(msg)))
    merged: List[AnyMessage] = []
    for msg in clean:
        if merged and type(merged[-1]) is type(msg):
            prev = merged[-1]
            prev_text = _message_content(prev)
            new_text = _message_content(msg)
            combined = f"{prev_text}\n\n{new_text}" if prev_text else new_text
            if isinstance(msg, HumanMessage):
                merged[-1] = HumanMessage(content=combined)
            else:
                merged[-1] = AIMessage(content=combined)
        else:
            merged.append(msg)
    return merged


def answer_probe(compressed_messages: List[AnyMessage], question: str) -> str:
    history = sanitize_for_continuation(compressed_messages)
    prompt = [SystemMessage(content=CONTINUATION_SYSTEM), *history, HumanMessage(content=question)]
    llm = get_llm()
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    return str(content or "").strip()


def grade_probe(
    *,
    probe_question: str,
    probe_type: str,
    reference_answer: str,
    continuation_text: str,
) -> Dict[str, Any]:
    prompt = build_judge_prompt(
        probe_question=probe_question,
        probe_type=probe_type,
        reference_answer=reference_answer,
        continuation_text=continuation_text,
    )
    llm = get_llm()
    response = llm.invoke(prompt)
    raw = response.content if hasattr(response, "content") else str(response)
    raw_str = str(raw or "")
    try:
        parsed = parse_judge_response(raw_str)
        parsed["judge_raw"] = raw_str
        parsed["parse_error"] = None
        return parsed
    except ValueError as exc:
        from evals.compression.rubric import DIMENSIONS

        return {
            "scores": {d: 0 for d in DIMENSIONS},
            "notes": "",
            "overall_probe_score": 0.0,
            "judge_raw": raw_str,
            "parse_error": str(exc),
        }


def grade_single_probe(
    compressed_messages: List[AnyMessage],
    probe: Dict[str, Any],
) -> Dict[str, Any]:
    continuation_text = answer_probe(compressed_messages, str(probe["question"]))
    judged = grade_probe(
        probe_question=str(probe["question"]),
        probe_type=str(probe["type"]),
        reference_answer=str(probe["reference_answer"]),
        continuation_text=continuation_text,
    )
    return {
        "probe_id": probe["id"],
        "type": probe["type"],
        "question": probe["question"],
        "reference_answer": probe["reference_answer"],
        "continuation_text": continuation_text,
        **judged,
    }
