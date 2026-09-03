"""定时任务接口用例（integration）：草稿解析/Cron 预览/任务 CRUD/启停/手动触发/运行历史。

此前整模块零覆盖。任务一律以 ``enabled=False`` 创建、测完删除，避免调度器
真的触发；``/parse`` 与手动触发涉及真实 LLM（异步执行），断言只钉端点
契约与记录落库，不等待任务产出的 Agent run 完成。

前置与运行：

    cd backend && uv run app.py
    uv run pytest tests/api/test_scheduled_tasks_api.py -m integration
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration]


def _create_disabled_task(auth_client, name: str) -> dict:
    resp = auth_client.post(
        "/api/user/scheduled-tasks",
        json={
            "name": name,
            "cron_expr": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "enabled": False,
            "qa_type": "COMMON_QA",
            "prompt": "用一句话汇报当前日期",
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]


def test_cron_preview(auth_client) -> None:
    resp = auth_client.get(
        "/api/user/scheduled-tasks/preview",
        params={"cron_expr": "0 9 * * 1-5", "timezone": "Asia/Shanghai"},
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    assert data["next_run_at"] and data["summary"]


@pytest.mark.llm
def test_parse_natural_language_returns_draft(auth_client) -> None:
    """NL 解析返回任务草稿（真实 LLM）。

    解析质量取决于当前模型对 JSON 输出的服从度：免费网关模型可能
    产出不可解析文本导致 400，属环境依赖，此时 skip 而非判失败。
    """
    resp = auth_client.post(
        "/api/user/scheduled-tasks/parse",
        json={"text": "每个工作日早上九点提醒我喝水"},
    )
    if resp.status_code == 400:
        pytest.skip(f"模型未能解析为任务草稿（环境依赖）: {resp.json().get('msg')}")
    resp.raise_for_status()
    draft = resp.json()["data"]
    assert isinstance(draft, dict) and draft


def _cleanup_tasks_by_prefix(auth_client, *prefixes: str) -> None:
    """按名称前缀兜底删除本用例创建的任务（幂等）。"""
    resp = auth_client.get("/api/user/scheduled-tasks")
    if resp.status_code != 200:
        return
    for t in resp.json()["data"]["tasks"]:
        if str(t.get("name", "")).startswith(prefixes):
            auth_client.delete(f"/api/user/scheduled-tasks/{t['id']}")


def test_task_crud_enable_disable_roundtrip(auth_client) -> None:
    """创建→列表/详情→更新→启用→停用→删除。"""
    name = f"接口验证任务-{uuid.uuid4().hex[:6]}"
    try:
        task = _create_disabled_task(auth_client, name)
        task_id = task["id"]
        resp = auth_client.get("/api/user/scheduled-tasks")
        resp.raise_for_status()
        assert any(t["id"] == task_id for t in resp.json()["data"]["tasks"])

        resp = auth_client.get(f"/api/user/scheduled-tasks/{task_id}")
        resp.raise_for_status()
        assert resp.json()["data"]["name"] == name

        resp = auth_client.put(
            f"/api/user/scheduled-tasks/{task_id}",
            json={"name": name + "-改名", "cron_expr": "30 8 * * *"},
        )
        resp.raise_for_status()
        assert resp.json()["data"]["cron_expr"] == "30 8 * * *"

        resp = auth_client.post(f"/api/user/scheduled-tasks/{task_id}/enable")
        resp.raise_for_status()
        assert resp.json()["data"]["enabled"] is True

        resp = auth_client.post(f"/api/user/scheduled-tasks/{task_id}/disable")
        resp.raise_for_status()
        assert resp.json()["data"]["enabled"] is False
    finally:
        _cleanup_tasks_by_prefix(auth_client, "接口验证任务-")

    resp = auth_client.get("/api/user/scheduled-tasks")
    resp.raise_for_status()
    assert all(t["id"] != task_id for t in resp.json()["data"]["tasks"])


@pytest.mark.llm
def test_manual_run_creates_run_record(auth_client) -> None:
    """手动触发：落运行记录并出现在运行历史；不等待 Agent run 终态。"""
    name = f"手动触发-{uuid.uuid4().hex[:6]}"
    try:
        task = _create_disabled_task(auth_client, name)
        task_id = task["id"]
        resp = auth_client.post(f"/api/user/scheduled-tasks/{task_id}/run")
        resp.raise_for_status()
        run_record = resp.json()["data"].get("run") or {}
        run_id = run_record.get("id")
        assert run_id, "手动触发应立即返回 queued 运行记录"

        resp = auth_client.get(
            f"/api/user/scheduled-tasks/{task_id}/runs",
            params={"page": 1, "page_size": 10},
        )
        resp.raise_for_status()
        history = resp.json()["data"]
        assert history.get("items"), "运行历史应包含刚触发的记录"

        resp = auth_client.get(f"/api/user/scheduled-task-runs/{run_id}")
        resp.raise_for_status()
        assert isinstance(resp.json()["data"], dict)
    finally:
        _cleanup_tasks_by_prefix(auth_client, "手动触发-")
