"""压缩摘要中间件修复的契约测试：指令位置 / 八节模板 / 复读检测 / 交接基调。"""

import pytest
from langchain_core.messages import HumanMessage

from noesis.agents.middlewares.compaction_middleware import _summary_is_invalid
from noesis.factory import COMPACTION_SUMMARY_INSTRUCTION


def test_instruction_is_structured_checkpoint():
    """八节模板逐节在场，含反元话语与 prior checkpoint 合并规则。"""
    for section in [
        "## Primary Request and Intent", "## Key Technical Concepts",
        "## Files and Code", "## Errors and Fixes", "## Pending Jobs",
        "## Current Work", "## Next Step", "## Critical Context",
    ]:
        assert section in COMPACTION_SUMMARY_INSTRUCTION
    assert "Do NOT mention this summarization request" in COMPACTION_SUMMARY_INSTRUCTION
    assert "PRIOR summary" in COMPACTION_SUMMARY_INSTRUCTION
    assert "rejected approaches" in COMPACTION_SUMMARY_INSTRUCTION


def test_instruction_is_handoff_not_budget_dump():
    """对齐 codex compact 的交接基调：不鼓励尽预算详尽（倾倒诱因），
    要求覆盖全部时间弧（近因偏置对症），质量防线在校验层不在请求层。"""
    assert "CONTEXT CHECKPOINT COMPACTION" in COMPACTION_SUMMARY_INSTRUCTION
    assert "handoff summary" in COMPACTION_SUMMARY_INSTRUCTION
    assert "output budget" not in COMPACTION_SUMMARY_INSTRUCTION
    assert "Be as detailed" not in COMPACTION_SUMMARY_INSTRUCTION
    assert "do not omit earlier phases" in COMPACTION_SUMMARY_INSTRUCTION
    assert "Be concise and structured" in COMPACTION_SUMMARY_INSTRUCTION


def test_summary_request_puts_instruction_last(monkeypatch):
    """指令必须作为最后一条 HumanMessage（头部指令在超长上下文实证全盲）。"""
    from types import SimpleNamespace

    from noesis.factory import _compaction_deps

    captured = {}

    class FakeModel:
        def invoke(self, request, config=None):
            captured["request"] = request
            return SimpleNamespace(text="## Primary Request and Intent\n- ok")

    monkeypatch.setattr(
        "noesis.factory.ModelConfig",
        SimpleNamespace(
            summarization_enabled=True, summarization_trigger_tokens=6000,
            summarization_trigger_fraction=0.75, max_tokens=8000,
            context_max_input_tokens=100000, summarization_messages_to_keep=28,
            summarization_user_message_tokens=20_000,
        ),
    )
    monkeypatch.setattr("noesis.factory.get_llm", lambda **kwargs: FakeModel())
    monkeypatch.setattr("noesis.factory.resolve_context_max_tokens", lambda _mid=None: 100000)

    deps = _compaction_deps(FakeModel(), "m1")
    history = [HumanMessage(content="早期消息"), HumanMessage(content="近期消息")]
    summary = deps["summarize"](history)
    assert summary.startswith("## Primary Request")

    request = captured["request"]
    assert len(request) == 3  # 原样 2 条 + 指令 1 条，不压扁成字符串
    assert request[0].content == "早期消息"
    assert request[-1].content == COMPACTION_SUMMARY_INSTRUCTION


def test_summary_is_invalid_catches_repetition_loop():
    """复读循环（745K 实证形态）必须判无效，走重试/熔断。"""
    loop = "\n".join(["## 您手动"] * 200)
    assert _summary_is_invalid(loop) is True
    # 倾倒式：行数多但唯一行占比极低
    dump = "\n".join(["## 您手动", "## 您手动", "## 您手动", "ok"] * 30)
    assert _summary_is_invalid(dump) is True


def test_summary_is_invalid_accepts_normal_checkpoint():
    normal = "\n".join([
        "## Primary Request and Intent", "- 实现记忆层",
        "## Key Technical Concepts", "- md frontmatter",
        "## Files and Code", "- src/a.py: 修复 X",
        "## Errors and Fixes", "- TypeError: 已修",
        "## Pending Jobs", "- (none)",
        "## Current Work", "- 收尾",
        "## Next Step", "- 跑测试",
        "## Critical Context", "- 正文补足最小体量：" + "约束与决策记录。 " * 120,
        "## Critical Context", "- 用户偏好中文",
    ])
    assert _summary_is_invalid(normal) is False
    assert _summary_is_invalid("") is True
    assert _summary_is_invalid("<error> boom") is True


def test_short_repetition_not_flagged():
    """短文本的重复不算复读（如列表中同一占位符多次出现）。

    正文垫到最小体量之上（否则先被过短判定拦下，测不到复读分支）。
    """
    short = "\n".join(["(none)"] * 6) + "\n" + "checkpoint filler section. " * 80
    assert _summary_is_invalid(short) is False
