"""接口测试：真实 LLM 全链路（FastAPI → 认证/CSRF → RunService → QaService → LLM → SSE → 落库）。

默认被 ``-m 'not integration'`` 排除；手动跑：

    cd backend && uv run app.py                         # 起服务
    uv run pytest tests/api/ -m integration -s
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.llm]


@pytest.mark.integration
def test_common_qa_happy_path(
    auth_client, create_session, create_run, consume_run_stream, gateway_skip
):
    """COMMON_QA 端到端：建会话 → 起 run → 消费 SSE → 校验落库。"""
    session_id = create_session(title="api-test-common-qa")
    run = create_run(
        session_id=session_id,
        content="用一句话介绍你自己",
        qa_type="COMMON_QA",
    )
    run_id = run["run_id"]

    metrics = consume_run_stream(auth_client, run_id)
    print(f"event_counts={metrics.event_counts} finish_reason={metrics.finish_reason} "
          f"error={metrics.error_message}")

    gateway_skip(metrics.error_message)
    assert metrics.succeeded, f"SSE 未成功结束: {metrics.error_message}"
    assert metrics.finish_reason == "stop"
    assert metrics.event_counts.get("text-delta", 0) > 0, "未收到 text-delta，疑似未走真实 LLM"

    # 服务端 authoritative 落库校验
    msg_resp = auth_client.get(f"/api/chat/sessions/{session_id}/messages")
    msg_resp.raise_for_status()
    messages = msg_resp.json()["data"]["messages"]
    roles = {m["role"] for m in messages}
    assert "user" in roles and "assistant" in roles

    assistant = next(m for m in messages if m["role"] == "assistant")
    parts = (assistant.get("content") or {}).get("parts") or []
    text_parts = [p for p in parts if p.get("type") == "text" and (p.get("content") or "").strip()]
    assert text_parts, f"assistant 消息缺少非空 text part: {parts}"


@pytest.mark.integration
def test_default_title_and_message_sequence_survive_refresh(
    auth_client, create_session, create_run, consume_run_stream
):
    """真实 PostgreSQL/API：首轮标题与 user→assistant 顺序由服务端持久化。"""
    session_id = create_session(title="新对话")
    run = create_run(
        session_id=session_id,
        content="验证刷新后的消息顺序",
        qa_type="COMMON_QA",
    )
    assert run["session_title"] == "验证刷新后的消息顺序"
    consume_run_stream(auth_client, run["run_id"])

    response = auth_client.get(f"/api/chat/sessions/{session_id}/messages")
    response.raise_for_status()
    messages = response.json()["data"]["messages"]
    sequences = [message["message_sequence"] for message in messages]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert [message["role"] for message in messages[-2:]] == ["user", "assistant"]

    cursor_response = auth_client.get(
        f"/api/chat/sessions/{session_id}/messages",
        params={"before_id": messages[-1]["id"]},
    )
    cursor_response.raise_for_status()
    earlier = cursor_response.json()["data"]["messages"]
    assert any(message["id"] == messages[-2]["id"] for message in earlier)


@pytest.mark.integration
def test_run_snapshot_terminal(auth_client, create_session, create_run, consume_run_stream, gateway_skip):
    """run 完成后 GET /runs/{run_id} 应为终态。"""
    session_id = create_session(title="api-test-snapshot")
    run = create_run(
        session_id=session_id,
        content="1+1 等于几？只回答数字",
        qa_type="COMMON_QA",
    )
    run_id = run["run_id"]

    consume_run_stream(auth_client, run_id)

    resp = auth_client.get(f"/api/chat/runs/{run_id}")
    resp.raise_for_status()
    snapshot = resp.json()["data"]
    if snapshot["status"] == "error":
        gateway_skip(snapshot.get("user_error_message"))
    assert snapshot["status"] in {"completed", "partial"}, f"非终态: {snapshot['status']}"
    assert snapshot.get("finish_reason"), "finish_reason 为空"


@pytest.mark.integration
def test_session_ensure_idempotent(auth_client):
    """PUT /sessions/{id}/ensure 同一 id 调两次应返回同一会话。"""
    session_id = str(uuid.uuid4())
    body = {"title": "api-test-ensure", "extra": {"qa_type": "COMMON_QA"}}

    first = auth_client.put(f"/api/chat/sessions/{session_id}/ensure", json=body)
    first.raise_for_status()
    second = auth_client.put(f"/api/chat/sessions/{session_id}/ensure", json=body)
    second.raise_for_status()

    assert first.json()["data"]["id"] == second.json()["data"]["id"] == session_id


@pytest.mark.integration
def test_stop_run(auth_client, create_session, create_run):
    """stop 一个长任务后 snapshot 应进入 partial/interrupted。"""
    session_id = create_session(title="api-test-stop", qa_type="SUPER_AGENT_QA")
    run = create_run(
        session_id=session_id,
        content="深度调研：列举三个主流向量数据库的对比",
        qa_type="SUPER_AGENT_QA",
    )
    run_id = run["run_id"]

    # 不消费流，直接停止
    stop_resp = auth_client.post(f"/api/chat/runs/{run_id}/stop")
    stop_resp.raise_for_status()
    snapshot = stop_resp.json()["data"]
    assert snapshot["status"] in {"partial", "interrupted", "completed"}, \
        f"停止后状态异常: {snapshot['status']}"
