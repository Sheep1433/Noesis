"""HITL 决策载荷归一化契约：pydantic→dict、拒绝默认文案统一。"""

from noesis.chat.hitl import normalize_hitl_decisions
from noesis.schemas.qa_vo import HitlDecisionItem


def test_pydantic_decisions_become_plain_dicts() -> None:
    """langchain HITL 中间件按下标取值：pydantic 对象必须转纯 dict。"""
    payloads = normalize_hitl_decisions([
        HitlDecisionItem(type="approve"),
        HitlDecisionItem(type="respond", message="补充说明"),
    ])
    assert payloads == [{"type": "approve"}, {"type": "respond", "message": "补充说明"}]
    assert all(isinstance(p, dict) for p in payloads)


def test_reject_without_message_gets_unified_default() -> None:
    """两个前端入口（任务面板/子会话抽屉）拒绝载荷不同：缺 message 统一补默认。"""
    payloads = normalize_hitl_decisions([
        HitlDecisionItem(type="reject"),
        {"type": "reject", "message": "用户拒绝了该操作"},
        {"type": "reject", "message": "自定义原因"},
    ])
    assert payloads[0] == {"type": "reject", "message": "用户拒绝了该操作"}
    assert payloads[1] == {"type": "reject", "message": "用户拒绝了该操作"}
    assert payloads[2] == {"type": "reject", "message": "自定义原因"}
