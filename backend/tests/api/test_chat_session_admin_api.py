"""会话管理面接口用例（integration）：真实 HTTP 走通管理类端点 happy path。

覆盖 ``test_chat_run_real_llm`` / ``test_chat_stream_contract`` 之外的会话
管理面：标题/置顶归档/已读/删除/批量删除、手动追加 user 消息、usage 汇总、
斜杠命令目录、工作区文件读写与打包下载、附件四件套、用户级与子 Agent 信令流。

前置与运行：

    cd backend && uv run app.py                         # 起服务（PG / Qdrant / MODEL_API_KEY）
    uv run pytest tests/api/test_chat_session_admin_api.py -m integration
"""

from __future__ import annotations

import json
import threading
import time

import pytest

pytestmark = [pytest.mark.integration]


def _create_session(auth_client, title: str) -> str:
    resp = auth_client.post("/api/chat/sessions", json={"title": title})
    resp.raise_for_status()
    return resp.json()["data"]["id"]


def _read_sse_first_matching(
    client,
    url: str,
    *,
    want_event: str,
    match,
    deadline_seconds: float,
) -> dict | None:
    """订阅 SSE 流，读到第一个满足 match 的指定 event 帧或超时。"""
    deadline = time.monotonic() + deadline_seconds
    with client.stream("GET", url) as response:
        response.raise_for_status()
        assert response.headers["content-type"].startswith("text/event-stream")
        pending_event: str | None = None
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                return None
            if line.startswith("event:"):
                pending_event = line[len("event:"):].strip() or None
            elif line.startswith("data:") and pending_event == want_event:
                raw = line[len("data:"):].strip()
                pending_event = None
                if not raw or raw == "[DONE]":
                    continue
                frame = json.loads(raw)
                if match(frame):
                    return frame
    return None


def test_session_admin_endpoints_roundtrip(auth_client) -> None:
    """标题/置顶/已读/手动消息/usage 汇总/删除/批量删除全链 happy path。"""
    session_id = _create_session(auth_client, "管理面原始标题")

    resp = auth_client.put(
        f"/api/chat/sessions/{session_id}/title", json={"title": "管理面改名后"}
    )
    resp.raise_for_status()
    assert resp.json()["data"]["title"] == "管理面改名后"

    resp = auth_client.put(
        f"/api/chat/sessions/{session_id}/meta", json={"pinned": True}
    )
    resp.raise_for_status()
    assert resp.json()["data"]["pinned"] is True

    resp = auth_client.put(f"/api/chat/sessions/{session_id}/read")
    assert resp.status_code == 200

    # 会话列表隐藏空会话：先落一条 user 消息再断言列表可见
    resp = auth_client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"content": "手动追加的 user 消息"},
    )
    resp.raise_for_status()
    message = resp.json()["data"]
    assert message["session_id"] == session_id and message["message_id"]

    resp = auth_client.get("/api/chat/sessions")
    resp.raise_for_status()
    listed = {s["id"]: s for s in resp.json()["data"]["sessions"]}
    assert session_id in listed, "有消息的会话应出现在列表"
    assert listed[session_id]["pinned"] is True, "置顶状态应反映在列表"

    resp = auth_client.get(f"/api/chat/sessions/{session_id}/messages")
    resp.raise_for_status()
    messages = resp.json()["data"]["messages"]
    assert any(
        m["role"] == "user" and "手动追加的 user 消息" in json.dumps(m["content"], ensure_ascii=False)
        for m in messages
    ), "手动消息应落库可读"

    resp = auth_client.get(f"/api/chat/sessions/{session_id}/usage-summary")
    resp.raise_for_status()
    data = resp.json().get("data")
    assert data is None or isinstance(data, dict), "无用量时 data 可为 null"

    resp = auth_client.delete(f"/api/chat/sessions/{session_id}")
    resp.raise_for_status()

    resp = auth_client.get("/api/chat/sessions")
    resp.raise_for_status()
    remaining = {s["id"] for s in resp.json()["data"]["sessions"]}
    assert session_id not in remaining, "删除后列表不应再包含该会话"


def test_batch_delete_sessions(auth_client) -> None:
    """batch-delete 软删多个会话并从列表消失。"""
    ids = [_create_session(auth_client, f"批量删除{i}") for i in range(2)]
    resp = auth_client.post("/api/chat/sessions/batch-delete", json={"session_ids": ids})
    resp.raise_for_status()
    assert f"已删除 {len(ids)}" in resp.json()["msg"]

    resp = auth_client.get("/api/chat/sessions")
    resp.raise_for_status()
    remaining = {s["id"] for s in resp.json()["data"]["sessions"]}
    assert not (set(ids) & remaining)


def test_commands_list(auth_client) -> None:
    """斜杠命令目录：非空且每项含 name/description。"""
    resp = auth_client.get("/api/chat/commands")
    resp.raise_for_status()
    items = resp.json()["data"]
    assert isinstance(items, list) and items, "命令目录不应为空"
    assert all("name" in item and "description" in item for item in items)


def test_workspace_file_roundtrip_and_archive(auth_client) -> None:
    """工作区文件读→写回→打包下载（端点只编辑既有文件，AGENTS.md 在白名单）。"""
    session_id = _create_session(auth_client, "工作区文件验证")
    try:
        resp = auth_client.get(
            f"/api/chat/sessions/{session_id}/workspace/file",
            params={"path": "AGENTS.md"},
        )
        resp.raise_for_status()
        original = resp.json()["data"]["content"]
        assert original.strip(), "用户根 AGENTS.md 应有内容"

        resp = auth_client.put(
            f"/api/chat/sessions/{session_id}/workspace/file",
            json={"path": "AGENTS.md", "content": original},
        )
        resp.raise_for_status()
        assert resp.json()["data"]["path"] == "AGENTS.md"

        resp = auth_client.get(
            f"/api/chat/sessions/{session_id}/workspace/archive",
            params={"path": "AGENTS.md"},
        )
        resp.raise_for_status()
        assert original in resp.text
        assert "attachment" in resp.headers.get("content-disposition", "")
    finally:
        auth_client.delete(f"/api/chat/sessions/{session_id}")


def test_attachment_roundtrip(auth_client) -> None:
    """附件上传→列表→预览→删除。"""
    session_id = _create_session(auth_client, "附件验证会话")
    try:
        resp = auth_client.post(
            f"/api/chat/sessions/{session_id}/attachments",
            files={"file": ("hello.txt", "附件内容验证".encode("utf-8"), "text/plain")},
        )
        resp.raise_for_status()
        attachment = resp.json()["data"]
        attachment_id = attachment["attachment_id"]
        assert attachment_id

        resp = auth_client.get(f"/api/chat/sessions/{session_id}/attachments")
        resp.raise_for_status()
        items = resp.json()["data"]["attachments"]
        assert any(a["attachment_id"] == attachment_id for a in items)

        artifact_url = attachment.get("artifact_url")
        if artifact_url:
            resp = auth_client.get(artifact_url)
            resp.raise_for_status()
            assert "附件内容验证" in resp.text

        resp = auth_client.delete(
            f"/api/chat/sessions/{session_id}/attachments/{attachment_id}"
        )
        resp.raise_for_status()
        resp = auth_client.get(f"/api/chat/sessions/{session_id}/attachments")
        resp.raise_for_status()
        items = resp.json()["data"]["attachments"]
        assert all(a["attachment_id"] != attachment_id for a in items)
    finally:
        auth_client.delete(f"/api/chat/sessions/{session_id}")


@pytest.mark.llm
def test_user_signal_stream_pushes_run_started(
    auth_client, create_session, create_run
) -> None:
    """用户级信令流：会话列表实时刷新数据源，创建 run 后应推 run-started。"""
    session_id = create_session(title="用户信令流验证")
    received: dict[str, dict | None] = {"frame": None}

    def pump() -> None:
        try:
            received["frame"] = _read_sse_first_matching(
                auth_client,
                "/api/chat/events/stream",
                want_event="user-signal",
                match=lambda f: f.get("type") == "run-started"
                and f.get("session_id") == session_id,
                deadline_seconds=60.0,
            )
        except BaseException:  # noqa: BLE001 - 线程边界传回主断言
            received["frame"] = None

    consumer = threading.Thread(target=pump)
    consumer.start()
    time.sleep(1.0)  # 等订阅建立再建 run，避免竞态漏帧
    run = create_run(session_id=session_id, content="1+1 等于几？", qa_type="COMMON_QA")
    consumer.join(timeout=70.0)

    frame = received["frame"]
    assert frame is not None, "用户信令流未推送本会话的 run-started 帧"
    assert frame.get("run_id") == run["run_id"]


def test_children_stream_keepalive_when_empty(auth_client) -> None:
    """子 Agent 目录事件流：无子任务的会话建连成功，空闲期只发 keepalive。"""
    session_id = _create_session(auth_client, "目录流空会话")
    try:
        deadline = time.monotonic() + 40.0
        with auth_client.stream(
            "GET", f"/api/chat/sessions/{session_id}/children/stream"
        ) as response:
            response.raise_for_status()
            assert response.headers["content-type"].startswith("text/event-stream")
            got_keepalive = False
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    break
                if line.startswith(":"):
                    got_keepalive = True
                    break
            assert got_keepalive, "空闲目录流应周期性发送 keepalive 注释帧"
    finally:
        auth_client.delete(f"/api/chat/sessions/{session_id}")
