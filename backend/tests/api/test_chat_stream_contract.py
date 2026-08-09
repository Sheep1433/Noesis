"""SSE 流式输出接口契约测试（真实 HTTP，``-m integration``）。

补 ``test_chat_run_real_llm.py`` 未覆盖的 SSE 接口分支：未知 run、终态重订阅、
停止后订阅、事件顺序、finish 契约、并发订阅广播、创建幂等等。

前置与运行：

    cd backend && uv run app.py                         # 起服务（PG / Qdrant / MODEL_API_KEY）
    uv run pytest tests/api/test_chat_stream_contract.py -m integration -s
"""

from __future__ import annotations

import threading
import uuid

import pytest

# 真实 LLM 单轮通常数秒；给足消费上限
STREAM_DEADLINE = 180


@pytest.mark.integration
def test_stream_unknown_run_returns_404(auth_client) -> None:
    """订阅不存在的 run 应返回 404，而非 500。"""
    resp = auth_client.get(f"/api/chat/runs/{uuid.uuid4().hex}/stream")
    assert resp.status_code == 404, resp.text


@pytest.mark.integration
def test_stop_unknown_run_returns_404(auth_client) -> None:
    """停止不存在的 run 应返回 404。"""
    resp = auth_client.post(f"/api/chat/runs/{uuid.uuid4().hex}/stop")
    assert resp.status_code == 404, resp.text


@pytest.mark.integration
def test_create_run_idempotent_by_client_request_id(
    auth_client, create_session
) -> None:
    """同一 client_request_id 重复创建应返回同一 run_id，不重复起任务。"""
    session_id = create_session(title="api-test-idempotent")
    request_id = uuid.uuid4().hex
    body = {
        "session_id": session_id,
        "content": "用一句话介绍你自己",
        "client_request_id": request_id,
        "extra": {"qa_type": "COMMON_QA"},
    }

    first = auth_client.post("/api/chat/runs", json=body)
    first.raise_for_status()
    second = auth_client.post("/api/chat/runs", json=body)
    second.raise_for_status()

    assert first.json()["data"]["run_id"] == second.json()["data"]["run_id"]


@pytest.mark.integration
def test_stream_first_frame_is_run_snapshot(
    auth_client, create_session, create_run, collect_run_stream
) -> None:
    """首轮 SSE 帧必须是 run-snapshot（权威快照先行）。"""
    session_id = create_session(title="api-test-first-frame")
    run = create_run(
        session_id=session_id,
        content="用三句话介绍太阳系",
        qa_type="COMMON_QA",
    )
    events = collect_run_stream(auth_client, run["run_id"], deadline_seconds=STREAM_DEADLINE)

    assert events.events, "未收到任何 SSE 帧"
    assert events.events[0][0] == "run-snapshot", f"首帧非 run-snapshot: {events.events[0]}"


@pytest.mark.integration
def test_message_start_precedes_first_text_delta(
    auth_client, create_session, create_run, collect_run_stream
) -> None:
    """message-start 必须早于首个 text-delta（前端按此建消息骨架）。"""
    session_id = create_session(title="api-test-event-order")
    run = create_run(
        session_id=session_id,
        content="写一首关于秋天的四行短诗",
        qa_type="COMMON_QA",
    )
    events = collect_run_stream(auth_client, run["run_id"], deadline_seconds=STREAM_DEADLINE)

    names = [name for name, _ in events.events]
    assert "text-delta" in names, "未收到 text-delta，疑似未走真实 LLM"
    assert "message-start" in names, "未收到 message-start"
    assert names.index("message-start") < names.index("text-delta"), names


@pytest.mark.integration
def test_finish_event_carries_finish_reason_before_done(
    auth_client, create_session, create_run, collect_run_stream
) -> None:
    """finish 帧带 finish_reason 且早于 [DONE]；[DONE] 是传输收尾，不进事件列表。"""
    session_id = create_session(title="api-test-finish-frame")
    run = create_run(
        session_id=session_id,
        content="1+1 等于几？只回答数字",
        qa_type="COMMON_QA",
    )
    events = collect_run_stream(auth_client, run["run_id"], deadline_seconds=STREAM_DEADLINE)

    assert events.done, f"未收到 [DONE]: {events.error}"
    assert events.events[-1][0] == "finish", f"末帧非 finish: {events.events[-1]}"
    finish_payload = events.events[-1][1]
    assert finish_payload["finish_reason"] == "stop", finish_payload


@pytest.mark.integration
def test_resubscribe_completed_run_emits_terminal_snapshot_and_done(
    auth_client, create_session, create_run, consume_run_stream, collect_run_stream
) -> None:
    """run 完成后再订阅：只发 run-snapshot（终态）+ [DONE]，不重跑 LLM、不发 text-delta。"""
    session_id = create_session(title="api-test-resubscribe")
    run = create_run(
        session_id=session_id,
        content="1+1 等于几？只回答数字",
        qa_type="COMMON_QA",
    )
    run_id = run["run_id"]
    # 先完整消费一轮，让 run 落到终态
    first = consume_run_stream(auth_client, run_id)
    assert first.succeeded, f"首轮未成功: {first.error_message}"

    # 再次订阅同一 run
    events = collect_run_stream(auth_client, run_id, deadline_seconds=STREAM_DEADLINE)

    assert events.done, "重订阅未收到 [DONE]"
    names = [name for name, _ in events.events]
    assert "run-snapshot" in names
    assert "text-delta" not in names, "终态重订阅不应再发 text-delta（疑似重跑 LLM）"
    assert "finish" not in names, "终态短路不应发 finish 帧"
    snapshot = next(p for name, p in events.events if name == "run-snapshot")
    assert snapshot["status"] in {"completed", "partial"}, snapshot["status"]


@pytest.mark.integration
def test_resubscribe_completed_run_snapshot_has_authoritative_content(
    auth_client, create_session, create_run, consume_run_stream, collect_run_stream
) -> None:
    """终态重订阅的 run-snapshot 应携带服务端权威落库的 assistant 文本内容。"""
    session_id = create_session(title="api-test-resubscribe-content")
    run = create_run(
        session_id=session_id,
        content="用一句话介绍你自己",
        qa_type="COMMON_QA",
    )
    run_id = run["run_id"]
    first = consume_run_stream(auth_client, run_id)
    assert first.succeeded, f"首轮未成功: {first.error_message}"

    events = collect_run_stream(auth_client, run_id, deadline_seconds=STREAM_DEADLINE)
    snapshot = next(p for name, p in events.events if name == "run-snapshot")
    parts = (snapshot.get("content") or {}).get("parts") or []
    text_parts = [
        p for p in parts
        if p.get("type") == "text" and (p.get("content") or "").strip()
    ]
    assert text_parts, f"终态快照缺少非空 text part: {parts}"


@pytest.mark.integration
def test_stop_then_stream_is_terminal(
    auth_client, create_session, create_run, collect_run_stream
) -> None:
    """停止长任务后订阅流：应收到终态 run-snapshot + [DONE]，run 状态为 partial/interrupted。"""
    session_id = create_session(title="api-test-stop-then-stream", qa_type="SUPER_AGENT_QA")
    run = create_run(
        session_id=session_id,
        content="深度调研：列举三个主流向量数据库并对比它们的核心特性",
        qa_type="SUPER_AGENT_QA",
    )
    run_id = run["run_id"]

    stop_resp = auth_client.post(f"/api/chat/runs/{run_id}/stop")
    stop_resp.raise_for_status()

    events = collect_run_stream(auth_client, run_id, deadline_seconds=STREAM_DEADLINE)
    assert events.done, f"停止后订阅未收到 [DONE]: {events.error}"

    # 权威状态：GET /runs/{id} 必为终态
    final = auth_client.get(f"/api/chat/runs/{run_id}")
    final.raise_for_status()
    assert final.json()["data"]["status"] in {"partial", "interrupted", "completed"}, \
        final.json()["data"]


@pytest.mark.integration
def test_concurrent_subscribers_both_receive_done(
    auth_client, create_session, create_run, collect_run_stream
) -> None:
    """同一 run 两个并发订阅都应收到完整流（RunEventBus 广播，无 SlowSubscriber 误逐）。"""
    session_id = create_session(title="api-test-concurrent")
    run = create_run(
        session_id=session_id,
        content="用约一百字介绍水的化学性质",
        qa_type="COMMON_QA",
    )
    run_id = run["run_id"]

    results: list = [None, None]

    def consume(idx: int) -> None:
        results[idx] = collect_run_stream(
            auth_client, run_id, deadline_seconds=STREAM_DEADLINE
        )

    t0 = threading.Thread(target=consume, args=(0,))
    t1 = threading.Thread(target=consume, args=(1,))
    t0.start()
    t1.start()
    t0.join()
    t1.join()

    for idx, r in enumerate(results):
        assert r is not None, f"订阅 {idx} 未返回"
        assert r.done, f"订阅 {idx} 未收到 [DONE]: {r.error}"
        assert r.error is None, f"订阅 {idx} 报错: {r.error}"
