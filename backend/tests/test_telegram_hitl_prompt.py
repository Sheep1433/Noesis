"""Telegram HITL 审批卡片 / callback 解析。"""
from __future__ import annotations

from domain.chat.delivery.telegram.hitl_prompt import (
    allow_session_grant_for_actions,
    build_approval_keyboard,
    decisions_for_op,
    format_hitl_card_text,
    is_network_execute,
    parse_hitl_callback_data,
    register_hitl_prompt,
    telegram_hitl_prompts,
)


def test_format_approval_card() -> None:
    text = format_hitl_card_text(
        {
            "kind": "approval",
            "action_requests": [
                {"name": "execute", "args": {"command": "ls -la"}},
            ],
        }
    )
    assert "需要审批" in text
    assert "ls -la" in text
    assert "批准" in text or "按钮" in text


def test_format_clarification_card() -> None:
    text = format_hitl_card_text(
        {
            "kind": "clarification",
            "action_requests": [
                {"name": "ask_user", "args": {"question": "选哪个？"}},
            ],
        }
    )
    assert "澄清" in text
    assert "选哪个" in text
    assert "回复文字" in text


def test_keyboard_and_callback_roundtrip() -> None:
    prompt = register_hitl_prompt(
        session_id="s1",
        user_id="1",
        chat_id="99",
        payload={
            "interrupt_id": "intr-1",
            "kind": "approval",
            "action_requests": [{"name": "write_file", "args": {"path": "/a"}}],
        },
    )
    kb = build_approval_keyboard(prompt.token, allow_session_grant=False)
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    assert len(rows[0]) == 2
    data_a = rows[0][0]["callback_data"]
    data_r = rows[0][1]["callback_data"]
    assert len(data_a) <= 64
    assert parse_hitl_callback_data(data_a) == (prompt.token, "a")
    assert parse_hitl_callback_data(data_r) == (prompt.token, "r")
    assert telegram_hitl_prompts.get(prompt.token) is not None
    telegram_hitl_prompts.pop(prompt.token)


def test_session_grant_row_for_network_execute() -> None:
    assert is_network_execute("execute", {"command": "curl https://x"})
    assert allow_session_grant_for_actions(
        [{"name": "execute", "args": {"command": "curl https://x"}}]
    )
    kb = build_approval_keyboard("abc123", allow_session_grant=True)
    assert len(kb["inline_keyboard"]) == 2
    assert parse_hitl_callback_data(kb["inline_keyboard"][1][0]["callback_data"]) == (
        "abc123",
        "s",
    )


def test_decisions_for_op() -> None:
    approve, scope = decisions_for_op("a", 2)
    assert scope is None
    assert approve == [{"type": "approve"}, {"type": "approve"}]
    reject, _ = decisions_for_op("r", 1)
    assert reject[0]["type"] == "reject"
    sess, g = decisions_for_op("s", 1)
    assert sess[0]["type"] == "approve"
    assert g == "session"
