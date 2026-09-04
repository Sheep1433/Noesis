"""会话信令 / 多窗口并发语义（真实 HTTP + 真实 LLM，``-m integration``）。

验证跨窗口发现机制的接口级语义：
1. 信令流在 run 创建时下发 ``run-started``，终态时下发 ``run-terminal``；
2. 同会话并发建 run → 409 + data.run_id（前端据此加入已有 run）；
3. 信令流建连即推当前 active run 首帧（「先连信令、后建 run」之外的所有时序）。

前置：
    cd backend && uv run app.py   # 手动启动服务（需 PG / Qdrant / 有效 MODEL_API_KEY）
    uv run pytest tests/api/test_session_signals_real.py -m integration -s
"""

from __future__ import annotations

import json
import time
from typing import Iterator

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.llm]

_SIGNAL_TIMEOUT_SECONDS = 120.0


def _read_signal_frames(
    client: httpx.Client,
    session_id: str,
    *,
    until: str,
    max_frames: int = 64,
    timeout_seconds: float = _SIGNAL_TIMEOUT_SECONDS,
) -> list[dict]:
    """订阅 /sessions/{id}/events，读到 type == until 的信令帧为止。"""
    deadline = time.perf_counter() + timeout_seconds
    frames: list[dict] = []
    with client.stream(
        "GET", f"/api/chat/sessions/{session_id}/events"
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if time.perf_counter() > deadline:
                pytest.fail(f"信令流超时未等到 {until}，已收帧: {frames}")
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if not raw or raw == "[DONE]":
                continue
            payload = json.loads(raw)
            if payload.get("type") == "session-signal" or "run_id" in payload:
                # 兼容首帧（event: session-signal，data 直接是信令对象）
                frames.append(payload)
                if payload.get("type") == until or payload.get("signal_type") == until:
                    return frames
            if len(frames) >= max_frames:
                pytest.fail(f"信令帧数超上限仍未等到 {until}: {frames}")
    pytest.fail(f"信令流提前结束，未等到 {until}，已收帧: {frames}")
    return frames


@pytest.fixture
def signal_session(create_session) -> Iterator[dict]:
    # create_session 返回会话 id 字符串；测试按 {"id": ...} 取用
    yield {"id": create_session(title="信令集成测试")}


def test_events_stream_emits_run_lifecycle(
    auth_client, signal_session, create_run
) -> None:
    """信令流：run-started 在创建时下发，run-terminal 在终态下发。"""
    import threading

    session_id = signal_session["id"]
    observed: dict[str, list[dict]] = {"frames": []}
    error: dict[str, BaseException | None] = {"exc": None}

    def pump_events() -> None:
        try:
            observed["frames"] = _read_signal_frames(
                auth_client, session_id, until="run-terminal"
            )
        except BaseException as exc:  # noqa: BLE001 - 线程边界传回主断言
            error["exc"] = exc

    consumer = threading.Thread(target=pump_events)
    consumer.start()
    # 等信令订阅建立（服务端 subscribe 入队后再建 run，避免竞态漏帧）
    time.sleep(1.0)

    run = create_run(
        session_id=session_id,
        content="用一句话回答：1+1 等于几？",
        qa_type="COMMON_QA",
    )
    consumer.join(timeout=_SIGNAL_TIMEOUT_SECONDS + 30.0)

    assert error["exc"] is None, f"信令消费线程异常: {error['exc']!r}"
    frames = observed["frames"]
    types = [f.get("type") for f in frames]
    assert "run-started" in types, f"未见 run-started: {types}"
    assert "run-terminal" in types, f"未见 run-terminal: {types}"

    started = next(f for f in frames if f.get("type") == "run-started")
    assert started.get("run_id") == run["run_id"]
    assert started.get("assistant_message_id") == run["assistant_message_id"]

    terminal = next(f for f in frames if f.get("type") == "run-terminal")
    assert terminal.get("run_id") == run["run_id"]
    assert terminal.get("status") in {"completed", "partial", "error", "interrupted"}


def test_concurrent_create_run_returns_409_with_joinable_fields(
    auth_client, signal_session, create_run
) -> None:
    """同会话第二个 run → 409 + data.run_id，前端据此加入已有 run。"""
    session_id = signal_session["id"]
    first = create_run(
        session_id=session_id,
        content="写一段 200 字左右的童话",
        qa_type="COMMON_QA",
    )
    second = auth_client.post(
        "/api/chat/runs",
        json={
            "session_id": session_id,
            "content": "第二个窗口的并发消息",
            "client_request_id": f"conflict-{first['run_id']}",
            "extra": {"qa_type": "COMMON_QA"},
        },
    )
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == 409
    assert body["msg"] == "当前会话仍在生成"
    assert body["data"]["run_id"] == first["run_id"]
    assert body["data"]["assistant_message_id"] == first["assistant_message_id"]

    # 排队窗口的消息未落库：用户消息列表仍只有第一条
    messages = auth_client.get(
        f"/api/chat/sessions/{session_id}/messages"
    ).json()["data"]["messages"]
    user_contents = [
        m["content"]["parts"][0].get("content", "")
        for m in messages
        if m["role"] == "user"
    ]
    assert "第二个窗口的并发消息" not in user_contents


def test_events_stream_first_frame_pushes_active_run(
    auth_client, signal_session, create_run
) -> None:
    """信令流建连时已有活跃 run：首帧立即下发 run-started（无需等新事件）。"""
    import threading

    session_id = signal_session["id"]
    run = create_run(
        session_id=session_id,
        content="写一段 200 字左右的散文",
        qa_type="COMMON_QA",
    )
    # run 仍在生成：建连即应收到当前 active run 的首帧
    frames: dict[str, list[dict]] = {"frames": []}

    def pump() -> None:
        try:
            frames["frames"] = _read_signal_frames(
                auth_client, session_id, until="run-started", timeout_seconds=30.0
            )
        except BaseException:  # noqa: BLE001 - 首帧断言在主线程做
            frames["frames"] = []

    consumer = threading.Thread(target=pump)
    consumer.start()
    consumer.join(timeout=45.0)

    started = [f for f in frames["frames"] if f.get("type") == "run-started"]
    assert started, "信令流建连未推 active run 首帧"
    assert started[0].get("run_id") == run["run_id"]
