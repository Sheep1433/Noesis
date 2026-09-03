"""后台子 Agent（async subagent）对话链路（真实 HTTP + 真实 LLM，``-m integration``）。

覆盖「对话触发后台任务」的接口级语义——单元层已有 executor 22 例全生命周期，
这里验证真实对话把链路串起来：
1. SuperAgent 对话调用 ``start_task`` → 子会话出现在 children 列表并到终态；
2. 任务终态后下一轮对话可正常完成（终态通知注入下一轮 prompt 的服务端路径）；
3. ``subagent-followup`` 错误路径契约（非子会话 → 404）。

前置：
    cd backend && uv run app.py
    uv run pytest tests/api/test_bg_subagent_real.py -m integration -s
"""

from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.llm]

_CHILD_POLL_TIMEOUT_SECONDS = 300.0
_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
_TERMINAL_RUN_STATUSES = {"completed", "partial", "error", "interrupted"}

START_TASK_PROMPT = (
    "请调用 start_task 工具启动一个后台子 Agent（run_in_background=true），"
    "任务描述：用一句话总结 HTTP 协议有哪些常用请求方法。"
    "启动后不要等待结果，立即回复我子 Agent 的会话 ID。"
)


def _wait_run_terminal(auth_client, run_id: str, *, timeout_seconds: float = 300.0) -> dict:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        snapshot = auth_client.get(f"/api/chat/runs/{run_id}").json()["data"]
        if snapshot.get("status") in _TERMINAL_RUN_STATUSES:
            return snapshot
        time.sleep(1.0)
    pytest.fail(f"run {run_id} 未在 {timeout_seconds}s 内到终态")


def _catalog_tasks(auth_client, session_id: str) -> list[dict]:
    data = auth_client.get(
        f"/api/chat/sessions/{session_id}/children/catalog"
    ).json()["data"]
    return data.get("tasks") or []


def _wait_subagent_terminal(auth_client, session_id: str) -> dict:
    """轮询 children/catalog 直到出现 subagent 任务并到终态。"""
    deadline = time.perf_counter() + _CHILD_POLL_TIMEOUT_SECONDS
    last: list[dict] = []
    while time.perf_counter() < deadline:
        last = _catalog_tasks(auth_client, session_id)
        subagents = [t for t in last if t.get("kind") == "subagent"]
        if subagents:
            task = subagents[0]
            if task.get("status") in _TERMINAL_TASK_STATUSES:
                return task
        time.sleep(2.0)
    pytest.fail(f"后台子 Agent 未在 {_CHILD_POLL_TIMEOUT_SECONDS}s 内到终态，目录: {last}")


def test_super_agent_launches_bg_subagent_to_terminal(
    auth_client, create_session, create_run
) -> None:
    """start_task 全链路：父对话终态 → children 列表出现子会话 → 任务到终态。"""
    session_id = create_session(title="后台子Agent集成测试", qa_type="SUPER_AGENT_QA")
    run = create_run(
        session_id=session_id, content=START_TASK_PROMPT, qa_type="SUPER_AGENT_QA"
    )
    parent = _wait_run_terminal(auth_client, run["run_id"])
    assert parent["status"] in {"completed", "partial"}, f"父 run 异常终态: {parent['status']}"

    # 子会话出现在 children 列表（kind=subagent）
    deadline = time.perf_counter() + 30.0
    children: list[dict] = []
    while time.perf_counter() < deadline:
        children = auth_client.get(
            f"/api/chat/sessions/{session_id}/children"
        ).json()["data"]["sessions"]
        if children:
            break
        time.sleep(1.0)
    assert children, "children 列表未出现子会话"
    child = children[0]
    assert child.get("kind") == "subagent"
    assert child.get("parent_id") == session_id

    # 任务在 catalog 中到终态，携带完成时间
    task = _wait_subagent_terminal(auth_client, session_id)
    assert task.get("child_session_id") or task.get("task_id")
    assert task.get("status") in _TERMINAL_TASK_STATUSES


def test_next_turn_after_bg_task_terminal(
    auth_client, create_session, create_run
) -> None:
    """任务终态后的下一轮对话正常完成（服务端注入终态通知的前置条件）。"""
    session_id = create_session(title="后台任务后续轮次测试", qa_type="SUPER_AGENT_QA")
    run = create_run(
        session_id=session_id, content=START_TASK_PROMPT, qa_type="SUPER_AGENT_QA"
    )
    _wait_run_terminal(auth_client, run["run_id"])
    _wait_subagent_terminal(auth_client, session_id)

    followup_run = create_run(
        session_id=session_id,
        content="后台任务完成了吗？请用一句话告诉我它的结果。",
        qa_type="SUPER_AGENT_QA",
    )
    snapshot = _wait_run_terminal(auth_client, followup_run["run_id"])
    assert snapshot["status"] in {"completed", "partial"}

    # 回复落历史且含非空文本
    message = auth_client.get(
        f"/api/chat/messages/{followup_run['assistant_message_id']}"
    ).json()["data"]
    parts = (message.get("content") or {}).get("parts") or []
    assert any(
        isinstance(p, dict) and p.get("type") == "text" and p.get("content", "").strip()
        for p in parts
    ), "后续轮次 assistant 无文本输出"


def test_subagent_followup_rejects_non_child_session(
    auth_client, create_session
) -> None:
    """subagent-followup 只接受子会话：普通会话/不存在的会话 → 404。"""
    session_id = create_session(title="followup 错误路径测试")

    missing = auth_client.post(
        f"/api/chat/sessions/{session_id}/subagent-followup",
        json={"message": "补充要求"},
    )
    assert missing.status_code == 404

    random_id = auth_client.post(
        "/api/chat/sessions/00000000-0000-0000-0000-000000000000/subagent-followup",
        json={"message": "补充要求"},
    )
    assert random_id.status_code == 404
