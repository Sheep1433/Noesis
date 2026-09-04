from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.chat.runs import RunStatus
from noesis.repositories.agent_run_repository import AgentRunRepository


@pytest.mark.asyncio
async def test_finalize_only_accepts_first_terminal_writer(monkeypatch) -> None:
    db = MagicMock()
    # execute 序列：①首个 finalize 的 run UPDATE（rowcount=1）
    # ②旧 extra SELECT ③assistant UPDATE（rowcount=1）；④第二个 finalize 的 run UPDATE（rowcount=0）
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1, fetchone=lambda: None),
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=0),
        ]
    )
    repository = AgentRunRepository(db)

    first = await repository.finalize(
        run_id="run-1",
        target=RunStatus.COMPLETED,
        assistant_status="completed",
        content={"parts": []},
        last_sequence=1,
        finished_at=1,
        finish_reason="stop",
    )
    second = await repository.finalize(
        run_id="run-1",
        target=RunStatus.PARTIAL,
        assistant_status="partial",
        content={"parts": []},
        last_sequence=2,
        finished_at=2,
        finish_reason="stopped",
    )

    assert first is True
    assert second is False
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_finalize_requires_assistant_compare_and_set() -> None:
    db = MagicMock()
    # execute 序列：run UPDATE（rowcount=1）→ 旧 extra SELECT → assistant UPDATE（rowcount=0）
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1, fetchone=lambda: None),
            SimpleNamespace(rowcount=0),
        ]
    )
    repository = AgentRunRepository(db)

    with pytest.raises(RuntimeError, match="assistant terminal"):
        await repository.finalize(
            run_id="run-1",
            target=RunStatus.ERROR,
            assistant_status="error",
            content={"parts": []},
            last_sequence=1,
            finished_at=1,
            finish_reason="error",
        )


@pytest.mark.asyncio
async def test_stale_checkpoint_does_not_touch_assistant() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    repository = AgentRunRepository(db)

    stored = await repository.save_checkpoint(
        run_id="run-1",
        assistant_message_id="message-1",
        sequence=4,
        snapshot={"parts": []},
        content={"parts": []},
        attempt_id=1,
        status=RunStatus.RUNNING,
        finish_reason=None,
        updated_at=1,
    )

    assert stored is False
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_list_claimable_queued_excludes_subagent_origin() -> None:
    """dispatcher 只 claim web run：subagent run 归进程内 executor 调度，
    且不带 launch_payload——被 claim 后必然 RUN_START_FAILED，排队任务整段
    对话丢失。"""
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(rowcount=0, scalars=lambda: MagicMock(all=lambda: []))
    )
    repository = AgentRunRepository(db)

    await repository.list_claimable_queued()

    statement = db.execute.await_args.args[0]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "t_agent_run.status = 'queued'" in compiled
    assert "t_agent_run.origin != 'subagent'" in compiled


@pytest.mark.asyncio
async def test_finalize_merges_usage_and_model_calls_across_runs() -> None:
    """HITL resume 同一 assistant 消息跨 run：usage 数值累加、model_calls 列表拼接。"""
    db = MagicMock()
    old_extra = {
        "qa_type": "SUPER_AGENT_QA",
        "usage": {"steps": 2.0, "input_tokens": 100.0, "output_tokens": 10.0},
        "model_calls": [{"step": 1, "model": "m"}, {"step": 2, "model": "m"}],
    }
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1, fetchone=lambda: [old_extra]),
            SimpleNamespace(rowcount=1),
        ]
    )
    repository = AgentRunRepository(db)

    won = await repository.finalize(
        run_id="run-2",
        target=RunStatus.COMPLETED,
        assistant_status="completed",
        content={"parts": []},
        last_sequence=5,
        finished_at=2,
        finish_reason="stop",
        usage={"steps": 3.0, "input_tokens": 200.0, "output_tokens": 20.0},
        model_calls=[{"step": 1, "model": "m"}, {"step": 2, "model": "m"}, {"step": 3, "model": "m"}],
    )
    assert won is True

    assistant_update = db.execute.await_args_list[2].args[0]
    extra = assistant_update.compile().params["extra"]
    assert extra["usage"]["input_tokens"] == 300.0
    assert extra["usage"]["steps"] == 5.0
    # 追加段 step 重编为全局连续序号（各 run 独立从 1 计数，不重复）
    assert [c["step"] for c in extra["model_calls"]] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_finalize_without_model_calls_leaves_key_absent() -> None:
    """非管道路径（取消/超时）不传 model_calls：不写空列表占位。"""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=1, fetchone=lambda: None),
            SimpleNamespace(rowcount=1),
        ]
    )
    repository = AgentRunRepository(db)

    won = await repository.finalize(
        run_id="run-3",
        target=RunStatus.PARTIAL,
        assistant_status="partial",
        content={"parts": []},
        last_sequence=1,
        finished_at=3,
        finish_reason="stopped",
    )
    assert won is True
    assistant_update = db.execute.await_args_list[2].args[0]
    extra = assistant_update.compile().params["extra"]
    assert "model_calls" not in extra
    assert "usage" not in extra
