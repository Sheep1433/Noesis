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


# ---------------------------------------------------------------------------
# claim fencing（worker-role-split Phase 1）：claim_epoch 递增 / heartbeat / 僵尸写拒绝
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_queued_returns_incrementing_epoch() -> None:
    """claim 成功返回认领后的 epoch（>0）；同批第二次 claim 输家返回 0。"""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(rowcount=1, fetchone=lambda: (1,)),
            SimpleNamespace(rowcount=1, fetchone=lambda: (3,)),
            SimpleNamespace(rowcount=0, fetchone=lambda: None),
        ]
    )
    repository = AgentRunRepository(db)

    assert await repository.claim_queued(
        run_id="run-1", owner_instance_id="w-1", owner_term=5, now_ms=100
    ) == 1
    # 对账重置（epoch 保留）后再认领：epoch 从上次值继续递增
    assert await repository.claim_queued(
        run_id="run-1", owner_instance_id="w-2", owner_term=5, now_ms=200
    ) == 3
    # owner 已被占用：输家 0
    assert await repository.claim_queued(
        run_id="run-1", owner_instance_id="w-3", owner_term=5, now_ms=300
    ) == 0


@pytest.mark.asyncio
async def test_claim_queued_writes_epoch_heartbeat_and_owner() -> None:
    """claim 的 UPDATE 递增 claim_epoch、写 heartbeat 与 owner（fencing 三件套）。"""
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(rowcount=1, fetchone=lambda: (2,))
    )
    repository = AgentRunRepository(db)

    epoch = await repository.claim_queued(
        run_id="run-1", owner_instance_id="w-1", owner_term=7, now_ms=1000
    )
    assert epoch == 2
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    assert compiled.params["owner_instance_id"] == "w-1"
    assert compiled.params["heartbeat_at"] == 1000
    # claim_epoch 为列自增表达式（参数化增量 claim_epoch_1=1），非字面量覆盖
    rendered = str(stmt)
    assert "claim_epoch=(t_agent_run.claim_epoch" in rendered
    assert compiled.params["claim_epoch_1"] == 1


@pytest.mark.asyncio
async def test_heartbeat_only_matches_owner_and_epoch() -> None:
    """心跳仅命中「本 worker + 本 epoch」；被重置/再认领后心跳失败。"""
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0)]
    )
    repository = AgentRunRepository(db)

    assert await repository.heartbeat(
        run_id="run-1", owner_instance_id="w-1", claim_epoch=2, now_ms=100
    ) is True
    # run 已被对账重置/再认领（epoch 3）：旧 worker 心跳落空
    assert await repository.heartbeat(
        run_id="run-1", owner_instance_id="w-1", claim_epoch=2, now_ms=200
    ) is False


@pytest.mark.asyncio
async def test_checkpoint_with_epoch_fencing_rejects_stale_writer() -> None:
    """带 claim_epoch 的 checkpoint 写：epoch 不符（僵尸）时 run 更新 0 行 → False。"""
    db = MagicMock()
    # ①run UPDATE rowcount=0（epoch 不符被拒）——不应推进到 assistant UPDATE
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    repository = AgentRunRepository(db)

    ok = await repository.save_checkpoint(
        run_id="run-1",
        assistant_message_id="msg-1",
        sequence=10,
        snapshot={"parts": []},
        content={"parts": []},
        attempt_id=1,
        status=RunStatus.RUNNING,
        finish_reason=None,
        updated_at=1,
        claim_epoch=2,
    )
    assert ok is False
    assert db.execute.await_count == 1  # 僵尸写止步于 run 行，未碰消息


@pytest.mark.asyncio
async def test_claim_next_batch_skips_capacity_full_and_collects_epochs() -> None:
    """批量认领：容量满的行跳过（保持 queued）、通过行收集 (run_id, epoch)。"""
    db = MagicMock()
    # ①圈行 SELECT 返回两行 ②③逐行 claim 的 RETURNING
    rows = MagicMock()
    rows.all.return_value = [("run-1", "user-1"), ("run-2", "user-2")]
    db.execute = AsyncMock(
        side_effect=[
            rows,
            SimpleNamespace(rowcount=1, fetchone=lambda: (4,)),
            SimpleNamespace(rowcount=1, fetchone=lambda: (2,)),
        ]
    )
    repository = AgentRunRepository(db)

    async def capacity_check(user_id: str) -> None:
        if user_id == "user-2":
            from noesis.chat.runs import RunCapacityExceeded
            raise RunCapacityExceeded("user limit")

    claimed = await repository.claim_next_batch(
        owner_instance_id="w-1", limit=10, now_ms=1, capacity_check=capacity_check
    )
    assert claimed == [("run-1", 4)]  # run-2 容量满：不认领，留给其他 worker
    # 圈行语句带 FOR UPDATE SKIP LOCKED（方言渲染差异：断言语句属性而非字符串）
    batch_stmt = db.execute.await_args_list[0].args[0]
    for_update = batch_stmt._for_update_arg
    assert for_update is not None and for_update.skip_locked


@pytest.mark.asyncio
async def test_claim_next_batch_without_capacity_check_claims_all() -> None:
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [("run-1", "u1")]
    db.execute = AsyncMock(
        side_effect=[rows, SimpleNamespace(rowcount=1, fetchone=lambda: (1,))]
    )
    repository = AgentRunRepository(db)

    claimed = await repository.claim_next_batch(
        owner_instance_id="w-1", limit=5, now_ms=1
    )
    assert claimed == [("run-1", 1)]
