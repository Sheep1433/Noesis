"""对话流接口调用（真实 HTTP + 真实 LLM，``-m integration``）。

覆盖一轮完整对话的接口面：多轮提问 → 消息历史（顺序/内容/时间透传）→
单条消息 → 会话标题 → active-run 发现。问题刻意选短回答，控制 LLM 成本。

前置：
    cd backend && uv run app.py   # 手动启动服务
    uv run pytest tests/api/test_conversation_flow_real.py -m integration -s
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.llm]

QUESTIONS = [
    "用一句话说明什么是 HTTP 200 状态码",
    "用一句话说明什么是 HTTP 404 状态码",
]


def _wait_terminal(auth_client, run_id: str, *, timeout_seconds: float = 180.0) -> dict:
    """轮询 GET /runs/{id} 直到终态（比订阅流简单，历史断言不依赖帧序）。"""
    import time

    deadline = time.perf_counter() + timeout_seconds
    terminal = {"completed", "partial", "error", "interrupted"}
    while time.perf_counter() < deadline:
        snapshot = auth_client.get(f"/api/chat/runs/{run_id}").json()["data"]
        if snapshot.get("status") in terminal:
            return snapshot
        time.sleep(1.0)
    pytest.fail(f"run {run_id} 未在 {timeout_seconds}s 内到终态")


def _user_texts(messages: list[dict]) -> list[str]:
    out = []
    for m in messages:
        if m.get("role") != "user":
            continue
        parts = (m.get("content") or {}).get("parts") or []
        text = "".join(
            p.get("content", "") for p in parts if isinstance(p, dict)
        ).strip()
        if text:
            out.append(text)
    return out


def test_multi_turn_conversation_history(
    auth_client, create_session, create_run
) -> None:
    """两轮提问：历史包含且仅包含所提问题，顺序与提问一致，序号严格递增。"""
    session_id = create_session(title="对话流集成测试")

    for question in QUESTIONS:
        run = create_run(session_id=session_id, content=question, qa_type="COMMON_QA")
        _wait_terminal(auth_client, run["run_id"])

    messages = auth_client.get(
        f"/api/chat/sessions/{session_id}/messages"
    ).json()["data"]["messages"]

    # 提问都在历史里（现在提问了哪些问题）
    assert _user_texts(messages) == QUESTIONS
    # user/assistant 交替且 assistant 已完成
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    for m in messages:
        if m["role"] == "assistant":
            assert m["status"] in {"completed", "partial"}
    # 消息序号严格递增
    seqs = [m["message_sequence"] for m in messages]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # assistant 消息携带 run 起止时间（run_started_at/run_finished_at 透传）
    for m in messages:
        if m["role"] == "assistant":
            assert m.get("run_started_at"), "assistant 缺 run_started_at"
            assert m.get("run_finished_at"), "assistant 缺 run_finished_at"


def test_get_assistant_message_by_id(
    auth_client, create_session, create_run
) -> None:
    """单条消息接口：assistant 消息可按 id 取回且 parts 非空。"""
    session_id = create_session(title="单条消息集成测试")
    run = create_run(
        session_id=session_id, content=QUESTIONS[0], qa_type="COMMON_QA"
    )
    _wait_terminal(auth_client, run["run_id"])

    message = auth_client.get(
        f"/api/chat/messages/{run['assistant_message_id']}"
    ).json()["data"]
    assert message["id"] == run["assistant_message_id"]
    parts = (message.get("content") or {}).get("parts") or []
    assert parts, "assistant 消息 parts 为空"
    assert any(
        isinstance(p, dict) and p.get("type") == "text" and p.get("content", "").strip()
        for p in parts
    ), "assistant 消息应含非空文本 part"


def test_session_title_after_first_question(
    auth_client, create_session, create_run
) -> None:
    """首轮问答后会话标题取自提问内容（不再是默认标题）。"""
    session_id = create_session(title="新对话")
    run = create_run(
        session_id=session_id, content=QUESTIONS[0], qa_type="COMMON_QA"
    )
    _wait_terminal(auth_client, run["run_id"])

    session = auth_client.get(f"/api/chat/sessions/{session_id}").json()["data"]
    title = session.get("title") or ""
    assert title and title != "新对话", f"标题未从提问生成: {title!r}"


def test_active_run_endpoint_lifecycle(
    auth_client, create_session, create_run
) -> None:
    """active-run 发现：run 进行中返回 run_id，终态后 data 为 null。"""
    session_id = create_session(title="active-run 集成测试")
    run = create_run(
        session_id=session_id,
        content="写一段 300 字左右的关于秋天的散文",
        qa_type="COMMON_QA",
    )

    snapshot = auth_client.get(
        f"/api/chat/sessions/{session_id}/active-run"
    ).json()["data"]
    # 轮询窗口内 run 可能已终态：两种状态断言其一成立即可
    if snapshot is not None:
        assert snapshot["run_id"] == run["run_id"]

    _wait_terminal(auth_client, run["run_id"])
    after = auth_client.get(
        f"/api/chat/sessions/{session_id}/active-run"
    ).json()["data"]
    assert after is None, f"终态后 active-run 应为 null，实际: {after and after.get('status')}"
