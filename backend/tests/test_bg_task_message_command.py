"""追加消息命令化单元测试：消费四分派、镜像查重、工具单一路径。

真实 PG 路径（受理事务、租约重置）见 test_bg_task_command_real.py
（integration 标记）。本文件用 monkeypatch 假体验证分派与映射逻辑。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from noesis.agents.background.executor import BackgroundTaskExecutor
from noesis.agents.background.jobs.state import BgTaskStatus
from noesis.errors.exceptions import ConflictException
from noesis.services.run_command_service import RunCommandConsumer


def _command_row(child_session_id: str, message_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="cmd-1",
        type="bg_task_deliver",
        payload={"child_session_id": child_session_id, "message_id": message_id},
        task_id=child_session_id,
        user_id="0b9e6c1e-0000-0000-0000-000000000001",
    )


def _message_payload(created_at: int = 1) -> dict[str, Any]:
    return {
        "pending": True, "text": "继续", "model_id": None,
        "reasoning_effort": None, "created_at": created_at,
    }


@pytest.fixture()
def consumer() -> RunCommandConsumer:
    return RunCommandConsumer(
        bus=None,
        scan_interval_seconds=5.0, retention_days=7.0,
    )


@pytest.fixture()
def command_env(monkeypatch):
    """消息载荷 + 内存/投影双查询的桩容器。"""
    class _Env:
        payload = _message_payload(created_at=1)
        memory_task: dict | None = None
        projection: dict | None = None
        flipped: list[str] = []
        delivered: list[str] = []

        def install(self, monkeypatch) -> None:
            from noesis.services.subagent_session_service import SubagentSessionService

            monkeypatch.setattr(
                SubagentSessionService, "message_delivery_payload",
                AsyncMock(side_effect=lambda *a, **k: dict(self.payload)),
            )
            monkeypatch.setattr(
                SubagentSessionService, "db_task_projection",
                AsyncMock(side_effect=lambda *a, **k: self.projection),
            )
            monkeypatch.setattr(
                SubagentSessionService, "flip_pending_message_dropped",
                AsyncMock(side_effect=lambda *a, **k: self.flipped.append(a[0]) or 1),
            )
            monkeypatch.setattr(
                BackgroundTaskExecutor, "get",
                staticmethod(lambda tid: self.memory_task),
            )

            consumer = self

            class _FakeExecutor:
                async def deliver_message(self, *args: Any, **kwargs: Any) -> dict:
                    consumer.delivered.append(str(kwargs.get("user_message_id") or args[2] if len(args) > 2 else ""))
                    return {"status": "running"}

            monkeypatch.setattr(
                BackgroundTaskExecutor, "default",
                classmethod(lambda cls: _FakeExecutor()),
            )

    env = _Env()
    env.install(monkeypatch)
    return env


@pytest.mark.asyncio
async def test_dispatch_row_not_pending_is_no_op(consumer, command_env, monkeypatch) -> None:
    """行已采纳/已翻转（非 pending）→ 幂等空转。"""
    command_env.payload = {"pending": False, "text": "x", "model_id": None,
                           "reasoning_effort": None, "created_at": 1}
    command_env.install(monkeypatch)
    summary = await consumer._deliver_bg_task(_command_row("child-1", "m-1"), "u1")
    assert summary is None


@pytest.mark.asyncio
async def test_dispatch_defers_when_running_but_hotset_miss(consumer, command_env, monkeypatch) -> None:
    """换主窗口（running + 热集 miss）→ 延后，SHALL NOT 翻转行。"""
    command_env.memory_task = None
    command_env.projection = {"status": "running", "completed_at": None}
    summary = await consumer._deliver_bg_task(_command_row("child-1", "m-1"), "u1")
    assert summary == "deferred:running"
    assert command_env.flipped == []


@pytest.mark.asyncio
async def test_dispatch_rejects_and_flips_when_not_revivable(consumer, command_env, monkeypatch) -> None:
    """任务不可续（failed/timed_out/error 收口）→ 翻转 dropped + 命令 rejected。"""
    command_env.memory_task = {"status": "failed", "completed_at": 100.0}
    with pytest.raises(ConflictException) as exc_info:
        await consumer._deliver_bg_task(_command_row("child-1", "m-1"), "u1")
    # ConflictException 自定义 __init__ 不入 str()：断言 message 属性
    assert "任务已结束" in (exc_info.value.message or "")
    assert command_env.flipped == ["m-1"]


@pytest.mark.asyncio
async def test_dispatch_cancelled_message_accepted_before_stop_is_retained(
    consumer, command_env, monkeypatch,
) -> None:
    """受理先于取消：命令 no_op 保留行（意图保留），任务 SHALL NOT 被复活。"""
    command_env.memory_task = {
        "status": "cancelled", "completed_at": 1000.0,  # 秒
    }
    command_env.payload["created_at"] = 500_000  # 毫秒，早于取消
    deliver_calls: list[str] = []

    class _FakeExecutor:
        async def deliver_message(self, *args: Any, **kwargs: Any) -> dict:
            deliver_calls.append(str(kwargs.get("user_message_id")))
            return {"status": "running"}

    monkeypatch.setattr(
        BackgroundTaskExecutor, "default",
        classmethod(lambda cls: _FakeExecutor()),
    )
    summary = await consumer._deliver_bg_task(_command_row("child-1", "m-1"), "u1")
    assert summary == "cancelled:queued_intent_retained"
    assert deliver_calls == []


@pytest.mark.asyncio
async def test_dispatch_cancelled_message_accepted_after_stop_revives(
    consumer, command_env, monkeypatch,
) -> None:
    """受理晚于取消（用户对已停任务的主动追问）→ 正常冷恢复复活。"""
    command_env.memory_task = {
        "status": "cancelled", "completed_at": 1000.0,
    }
    command_env.payload["created_at"] = 2_000_000  # 毫秒，晚于取消
    deliver_calls: list[str] = []

    class _FakeExecutor:
        async def deliver_message(self, *args: Any, **kwargs: Any) -> dict:
            deliver_calls.append(str(kwargs.get("user_message_id")))
            return {"status": "running"}

    monkeypatch.setattr(
        BackgroundTaskExecutor, "default",
        classmethod(lambda cls: _FakeExecutor()),
    )
    summary = await consumer._deliver_bg_task(_command_row("child-1", "m-1"), "u1")
    assert summary == "delivered"
    assert deliver_calls == ["m-1"]


@pytest.mark.asyncio
async def test_dispatch_delivers_running_task(consumer, command_env, monkeypatch) -> None:
    """任务 running 且在热集：入执行镜像队列（deliver_message 消费）。"""
    command_env.memory_task = {"status": "running", "completed_at": None}
    summary = await consumer._deliver_bg_task(_command_row("child-1", "m-1"), "u1")
    assert summary == "delivered"


def test_tool_send_message_goes_through_accept(monkeypatch) -> None:
    """单一路径：模型工具 send_message 经 accept_message（命令受理），SHALL NOT
    本地直通 executor.deliver_message。"""
    import noesis.agents.background.ports as bg_ports
    from noesis.agents.background.subagent.tools import AsyncSubagentToolsMiddleware
    from noesis.agents.background.subagent.roles import SubagentRegistry

    calls: list[dict] = []

    class _FakeService:
        async def accept_message(self, **kwargs):
            calls.append(kwargs)
            return {"task_id": "child-1", "child_session_id": "child-1",
                    "status": "running", "session_id": "parent-1",
                    "description": "x", "user_id": "u1"}

    bg_ports.configure_service_port(_FakeService())
    registry = SubagentRegistry()
    executor = BackgroundTaskExecutor()
    middleware = AsyncSubagentToolsMiddleware(
        registry=registry, executor=executor,
        session_id="parent-1", user_id="u1",
        create_turn_run=None,
    )
    send_message = next(t for t in middleware.tools if t.name == "send_message")

    async def _invoke():
        return await send_message.coroutine("child-1", "继续", tool_call_id="tc-1")

    result = asyncio.run(_invoke())
    assert calls and calls[0]["task_ref"] == "child-1"
    tool_text = result.update["messages"][0].content
    assert "消息已提交" in tool_text
    assert "child-1" in result.update["async_tasks"]


def test_dispatch_missing_payload_is_no_op(consumer, monkeypatch) -> None:
    row = SimpleNamespace(id="cmd-2", type="bg_task_deliver", payload={}, task_id=None)
    assert asyncio.run(consumer._deliver_bg_task(row, "u1")) is None
