"""真实 DB 的停止收口集成测试。

覆盖单测盲区：任务携带真实 run_id / child_session_id / assistant_message_id 时，
停止收口经 SubagentSessionPort 调用真实服务——collect_partial_output 从落库
投影回收部分成果、mark_terminal 把 run 行终态化。端口缺方法的事故（collect_
partial_output 只加在服务上漏了端口委托）正藏在这个盲区：单测任务无 run_id，
端口调用根本不会执行。

两个场景共用一个事件循环（pg 引擎池与捕获的主 loop 均绑定首个 loop）：
1. 协作停止：静止边界退出 → 部分成果（含 outcome 兜底）+ run 落 partial；
2. 硬取消（outcome=None）：部分成果只能来自端口读真实落库投影——对端口
   缺方法免疫的区分性路径（协作场景的 outcome 兜底会掩盖该缺口）。

前置：``cd backend && set -a && source .env && set +a`` 后
``NOESIS_LIVE_POSTGRES_TEST=1 uv run pytest tests/test_subagent_stop_real_db.py -m integration``
（默认在常规全量中被 -m 过滤跳过）。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from noesis.agents.subagents.executor import (
    BackgroundTaskExecutor,
    BgTaskStatus,
    shutdown as bg_shutdown,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("NOESIS_LIVE_POSTGRES_TEST") != "1",
        reason="设置 NOESIS_LIVE_POSTGRES_TEST=1 后运行真实 PostgreSQL 收口集成测试",
    ),
]

_PRE_STOP_TEXT = "停止前已产出的分析文本。"


class _ScriptedToolModel(BaseChatModel):
    """按脚本依次返回 AIMessage（可带 tool_calls）；bind_tools 返回自身。"""

    script: list[AIMessage]
    _cursor: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted-stop-integration"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        idx = self._cursor
        self._cursor += 1
        message = self.script[idx] if idx < len(self.script) else AIMessage(content="任务完成")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _build_worker() -> object:
    @tool
    def slow(value: str) -> str:
        """A tool that takes a while, keeping the agent running."""
        time.sleep(0.6)
        return f"slow:{value}"

    first = AIMessage(
        content=_PRE_STOP_TEXT,
        tool_calls=[{"name": "slow", "args": {"value": "s0"}, "id": "c0", "type": "tool_call"}],
    )
    rest = [
        AIMessage(
            content="",
            tool_calls=[{"name": "slow", "args": {"value": f"s{i}"}, "id": f"c{i}", "type": "tool_call"}],
        )
        for i in range(1, 20)
    ]
    return create_agent(
        _ScriptedToolModel(script=[first] + rest),
        tools=[slow],
        checkpointer=MemorySaver(),
        name="task-worker",
    )


async def _fetch_user_id(db) -> str:
    from sqlalchemy import select
    from noesis.storage.postgres.models.auth import TUser

    user_id = (await db.execute(select(TUser.id).limit(1))).scalar_one_or_none()
    assert user_id, "数据库无用户，无法构造子 Agent 会话"
    return str(user_id)


async def _insert_child_rows(db, user_id: str) -> dict[str, str]:
    """父/子会话 + 首条 user/assistant 消息 + run 行（复刻 launch 的落库形状）。"""
    from noesis.chat.runs import RunStatus as _RunStatus
    from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage, TChatSession

    now = int(time.time() * 1000)
    parent_id, child_id, run_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    user_message_id, assistant_message_id = str(uuid.uuid4()), str(uuid.uuid4())
    # 分批 flush 保证 FK 顺序：会话 → 消息 → run（UOW 按表名排序不可依赖，
    # 与 launch 的「循环依赖分批 flush」同一约束）
    db.add_all([
        TChatSession(id=parent_id, user_id=user_id, title="停止收口集成测试",
                     created_at=now, updated_at=now, next_message_sequence=1),
        TChatSession(id=child_id, parent_id=parent_id, user_id=user_id, title="子任务",
                     kind="subagent", extra={"origin": "subagent"},
                     created_at=now, updated_at=now, next_message_sequence=3),
    ])
    await db.flush()
    db.add_all([
        TChatMessage(id=user_message_id, session_id=child_id, user_id=user_id, role="user",
                     content={"parts": [{"type": "text", "content": "做点研究"}]},
                     status="completed", message_sequence=1, created_at=now),
        TChatMessage(id=assistant_message_id, session_id=child_id, parent_id=user_message_id,
                     user_id=user_id, role="assistant", content={"parts": []},
                     extra={"origin": "subagent", "run_id": run_id},
                     status="streaming", message_sequence=2, created_at=now),
    ])
    await db.flush()
    db.add(TAgentRun(id=run_id, session_id=child_id, user_id=user_id, origin="subagent",
                     qa_type="SUPER_AGENT_QA", client_request_id=f"subagent:{run_id}",
                     request_digest=f"integration:{run_id}",
                     status=_RunStatus.RUNNING.value, created_at=now, started_at=now,
                     updated_at=now, assistant_message_id=assistant_message_id))
    await db.commit()
    return {
        "parent_id": parent_id, "child_id": child_id, "run_id": run_id,
        "assistant_message_id": assistant_message_id,
    }


async def _cleanup_rows(db, ids: dict[str, str]) -> None:
    from sqlalchemy import delete
    from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage, TChatSession

    await db.execute(delete(TChatMessage).where(TChatMessage.session_id == ids["child_id"]))
    await db.execute(delete(TAgentRun).where(TAgentRun.id == ids["run_id"]))
    await db.execute(delete(TChatSession).where(
        TChatSession.id.in_([ids["parent_id"], ids["child_id"]])))
    await db.commit()


async def _wait_first_projection(pg_manager, assistant_message_id: str) -> None:
    """轮询直到首个投影落库（停止前文本进入 assistant 消息）。"""
    from sqlalchemy import select
    from noesis.storage.postgres.models.chat import TChatMessage

    deadline = time.perf_counter() + 30
    while time.perf_counter() < deadline:
        async with pg_manager.get_async_session_context() as db:
            content = (await db.execute(
                select(TChatMessage.content).where(TChatMessage.id == assistant_message_id)
            )).scalar_one()
        if any(p.get("content") for p in content.get("parts", [])):
            return
        await asyncio.sleep(0.2)
    pytest.fail("首个投影未在 30s 内落库")


async def _fetch_run_and_message_status(pg_manager, ids: dict[str, str]) -> tuple:
    from sqlalchemy import select
    from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage

    async with pg_manager.get_async_session_context() as db:
        run = (await db.execute(
            select(TAgentRun).where(TAgentRun.id == ids["run_id"])
        )).scalar_one()
        message_status = (await db.execute(
            select(TChatMessage.status).where(TChatMessage.id == ids["assistant_message_id"])
        )).scalar_one()
    return run, message_status


async def _wait_cancelled(executor: BackgroundTaskExecutor, task_id: str) -> dict:
    deadline = time.perf_counter() + 30
    while time.perf_counter() < deadline:
        task = executor.get(task_id)
        if task and task["status"] == BgTaskStatus.CANCELLED.value:
            return task
        await asyncio.sleep(0.1)
    pytest.fail("停止未在 30s 内收口")


async def _run_stop_scenario(pg_manager, *, hard_kill: bool) -> None:
    """启动带真实 run 的子任务 → 停止 → 校验部分成果与 run 终态。"""
    async with pg_manager.get_async_session_context() as db:
        user_id = await _fetch_user_id(db)
        ids = await _insert_child_rows(db, user_id)

    executor = BackgroundTaskExecutor(
        task_timeout_seconds=60, stop_grace_seconds=1, stop_reconcile_seconds=1,
    )
    try:
        task_id = executor.start(
            worker_factory=_build_worker,
            description="硬取消收口集成" if hard_kill else "停止收口集成",
            session_id=ids["parent_id"],
            user_id=user_id,
            child_session_id=ids["child_id"],
            run_id=ids["run_id"],
            assistant_message_id=ids["assistant_message_id"],
        )
        await _wait_first_projection(pg_manager, ids["assistant_message_id"])
        assert executor.cancel(task_id)["status"] == BgTaskStatus.STOPPING.value

        if hard_kill:
            # 绕过墙钟直接触发宽限超时硬杀（真实路径为 call_later 回调）；
            # CancelledError → _finalize_stop(outcome=None)：部分成果唯一来源
            # 是端口读真实落库投影
            import noesis.agents.subagents.executor as executor_mod

            with executor_mod._TASKS_LOCK:
                entry = executor_mod._TASKS[task_id]
            executor_mod._on_stop_grace_timeout(entry)

        task = await _wait_cancelled(executor, task_id)
        assert task["result"] and task["result"].startswith("中止前部分产出")
        assert _PRE_STOP_TEXT in task["result"], (
            "部分成果未回收：硬杀场景只能来自端口 collect_partial_output 读 DB"
        )

        run, message_status = await _fetch_run_and_message_status(pg_manager, ids)
        assert run.status == "partial", f"run 终态异常: {run.status}"
        assert run.finish_reason == "cancelled"
        assert message_status == "partial"
    finally:
        bg_shutdown()
        async with pg_manager.get_async_session_context() as db:
            await _cleanup_rows(db, ids)


async def test_stop_finalize_real_run_covers_cooperative_and_hard_kill() -> None:
    """协作停止与硬取消两个场景（同一事件循环，pg 引擎池绑定首个 loop）。"""
    import noesis.services.subagent_session_service  # noqa: F401  模块导入即注册端口
    from noesis.runtime.main_loop import capture_main_loop
    from noesis.storage.postgres.manager import pg_manager

    capture_main_loop()
    assert pg_manager.initialize() is not False, "PostgreSQL 初始化失败"

    await _run_stop_scenario(pg_manager, hard_kill=False)
    await _run_stop_scenario(pg_manager, hard_kill=True)
