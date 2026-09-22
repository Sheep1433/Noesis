"""双进程 leader 选举与晋升集成测试（enable-distributed-sse-pubsub task 2.3/2.4）。

真实 PostgreSQL（advisory lock + t_runtime_leader）与真实 Redis（run bus），
两个 LeaderElector 模拟双 backend 进程：唯一 leader、follower 待命与重竞选、
leader 释放后 follower 晋升（晋升回调触发 + term 递增）、memory 语义的
fail-fast 保持。singleton 绑定与四段 recovery 的进程级编排由 lifespan 测试
与单测覆盖；本文件聚焦选举协议本身。

前置：``cd backend && set -a && source .env && set +a`` 后
``NOESIS_LIVE_POSTGRES_TEST=1 TEST_REDIS_URL=redis://localhost:16379/0 uv run pytest tests/test_leader_election_two_process.py -m integration``
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("NOESIS_LIVE_POSTGRES_TEST") != "1",
        reason="设置 NOESIS_LIVE_POSTGRES_TEST=1 后运行真实 PostgreSQL 选举集成测试",
    ),
]


async def _ping_redis() -> bool:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")
        )
        try:
            await asyncio.wait_for(client.ping(), timeout=1.5)
            return True
        finally:
            await client.aclose()
    except Exception:
        return False


async def test_two_electors_unique_leader_and_promotion() -> None:
    from noesis.services.leader_elector import LeaderElector
    from noesis.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    assert await _ping_redis(), "本用例需要真实 Redis（TEST_REDIS_URL）"

    cluster = "two-proc-test"
    # t_runtime_leader 是全局单行（cluster identity 固定）：本地库可能已被
    # dev 进程用其它 cluster_id 初始化——测试用例内重置该行（仅本地测试库）
    from sqlalchemy import delete

    from noesis.storage.postgres.models.runtime_leader import TRuntimeLeader

    async with pg_manager.get_async_session_context() as db:
        await db.execute(delete(TRuntimeLeader))
        await db.commit()

    elector_a = LeaderElector(cluster_id=cluster)
    elector_b = LeaderElector(cluster_id=cluster)

    # A 先获锁（memory 语义 acquire：fail-fast）
    token_a = await elector_a.acquire()
    assert token_a.valid and elector_a.is_leader

    # B 以 redis 语义 run_as_worker：拿不到锁 → follower 待命（不抛错）
    promoted_b = asyncio.Event()
    promotions_b: list[int] = []

    async def on_promotion_b(token):
        promotions_b.append(token.term)
        promoted_b.set()

    worker_task = asyncio.create_task(elector_b.run_as_worker(on_promotion=on_promotion_b))
    # run_as_worker 在未获锁时返回（后台重竞选任务接管）：等首分支完成
    for _ in range(200):
        if worker_task.done() or promoted_b.is_set():
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    assert not elector_b.is_leader, "A 持锁期间 B 不得为 leader"
    assert not promoted_b.is_set(), "B 不得晋升"

    # A 释放（模拟 leader 下线）→ B 在重竞选周期内晋升，term 递增
    await elector_a.release()
    deadline = time.monotonic() + LeaderElector._RE_ELECTION_INTERVAL_SECONDS + 5
    while time.monotonic() < deadline:
        if promoted_b.is_set():
            break
        await asyncio.sleep(0.2)
    assert promoted_b.is_set(), "A 释放后 B 应在重竞选周期内晋升"
    assert promotions_b[0] > token_a.term, "晋升 term 必须递增"
    assert elector_b.is_leader

    # B 晋升后 A 不得再 claim（旧 token 已失效）；同 manager 已持锁时
    # try 语义返回 False（他人持有）
    assert not elector_a.is_leader
    assert not await pg_manager.try_advisory_lock(), "B 持锁期间不得再获锁"

    # B 持锁期间：第二进程语义的 acquire 必须 fail-fast（独立连接直验 PG
    # 互斥——同进程 pg_manager 已持锁时 acquire 是幂等 no-op，模拟不了第二进程）
    import asyncpg
    from urllib.parse import quote_plus

    from noesis.config.env import DataBaseConfig

    dsn = (
        f"postgresql://{DataBaseConfig.postgres_user}:"
        f"{quote_plus(DataBaseConfig.postgres_password)}@"
        f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/"
        f"{DataBaseConfig.postgres_database}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        second_got = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", 0x4E6F6573, 0x69735F61
        )
        assert second_got is False, "B 持锁期间第二进程不得获锁（memory fail-fast 语义）"
    finally:
        await conn.close()

    await elector_b.release()
    worker_task.cancel()
    try:
        await worker_task
    except (asyncio.CancelledError, Exception):
        pass


