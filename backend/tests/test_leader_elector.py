"""Leader elector 与 migration lock 测试（live PostgreSQL 门控）。

对应 openspec enable-distributed-sse-pubsub task 2.1 / 2.2。
门控与 tests/test_advisory_lock.py 相同：NOESIS_LIVE_POSTGRES_TEST=1。
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import quote_plus

import pytest

from noesis.config.env import DataBaseConfig
from noesis.repositories.runtime_leader_repository import (
    ClusterIdMismatchError,
    RuntimeLeaderRepository,
)
from noesis.services.leader_elector import LeaderElector, LeadershipLostError
from noesis.storage.postgres.manager import pg_manager


def _can_connect_postgres() -> bool:
    import asyncpg

    dsn = (
        f"postgresql://{DataBaseConfig.postgres_user}:"
        f"{quote_plus(DataBaseConfig.postgres_password)}@"
        f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/"
        f"{DataBaseConfig.postgres_database}"
    )

    async def _try() -> bool:
        try:
            conn = await asyncpg.connect(dsn)
            await conn.close()
            return True
        except Exception:
            return False

    return asyncio.get_event_loop().run_until_complete(_try())


def _execution_lock_is_free() -> bool:
    """执行锁必须空闲（dev server 运行中持有锁时这些用例让位）。"""
    import asyncpg

    dsn = (
        f"postgresql://{DataBaseConfig.postgres_user}:"
        f"{quote_plus(DataBaseConfig.postgres_password)}@"
        f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/"
        f"{DataBaseConfig.postgres_database}"
    )

    async def _try() -> bool:
        from noesis.storage.postgres.manager import (
            _NOESIS_ADVISORY_LOCK_KEY1,
            _NOESIS_ADVISORY_LOCK_KEY2,
        )

        try:
            conn = await asyncpg.connect(dsn)
            try:
                acquired = await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1, $2)",
                    _NOESIS_ADVISORY_LOCK_KEY1,
                    _NOESIS_ADVISORY_LOCK_KEY2,
                )
                if acquired:
                    await conn.fetchval(
                        "SELECT pg_advisory_unlock($1, $2)",
                        _NOESIS_ADVISORY_LOCK_KEY1,
                        _NOESIS_ADVISORY_LOCK_KEY2,
                    )
                    return True
                return False
            finally:
                await conn.close()
        except Exception:
            return False

    return asyncio.get_event_loop().run_until_complete(_try())


requires_live_postgres = pytest.mark.skipif(
    os.getenv("NOESIS_LIVE_POSTGRES_TEST") != "1"
    or not _can_connect_postgres()
    or not _execution_lock_is_free(),
    reason="设置 NOESIS_LIVE_POSTGRES_TEST=1 且执行锁空闲（停掉 dev server）后运行",
)


@pytest.mark.asyncio
async def test_leadership_token_semantics_without_db() -> None:
    """token 不可复用：invalidate 后 require_valid 抛错（纯单元）。"""
    from noesis.services.leader_elector import LeadershipToken

    token = LeadershipToken(term=3, instance_id="i", cluster_id="local")
    assert token.valid and token.term == 3
    token._invalidate()
    assert not token.valid
    with pytest.raises(LeadershipLostError):
        token.require_valid()


@requires_live_postgres
@pytest.mark.asyncio
async def test_elector_acquires_increasing_term_and_blocks_other_instance() -> None:
    """获锁提交 term（递增）；其它连接无法获取同一执行锁（跨实例 fail-fast 语义）。"""
    import asyncpg

    await pg_manager.acquire_advisory_lock()
    try:
        async with pg_manager.get_async_session_context() as db:
            previous_term = await RuntimeLeaderRepository(db).get_current_term(
                cluster_id="local"
            ) or 0
        elector = LeaderElector(cluster_id="local")
        token = await elector.acquire()
        assert token.valid
        assert token.term == previous_term + 1

        # 第二实例语义：进程外连接竞争同一锁必然失败（进程内 acquire 幂等，
        # 不能用同一 pg_manager 模拟第二实例——跨实例 fail-fast 由
        # tests/test_advisory_lock.py 与真实双进程冒烟覆盖）
        dsn = (
            f"postgresql://{DataBaseConfig.postgres_user}:"
            f"{quote_plus(DataBaseConfig.postgres_password)}@"
            f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/"
            f"{DataBaseConfig.postgres_database}"
        )
        conn = await asyncpg.connect(dsn)
        try:
            from noesis.storage.postgres.manager import (
                _NOESIS_ADVISORY_LOCK_KEY1,
                _NOESIS_ADVISORY_LOCK_KEY2,
            )

            acquired = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1, $2)",
                _NOESIS_ADVISORY_LOCK_KEY1,
                _NOESIS_ADVISORY_LOCK_KEY2,
            )
            assert acquired is False
        finally:
            await conn.close()

        await elector.release()
    finally:
        await pg_manager.close()


@requires_live_postgres
@pytest.mark.asyncio
async def test_foreign_cluster_id_fails_fast() -> None:
    """t_runtime_leader 存在其它 cluster_id 行：拒绝启动（防两套独立 term 序列）。"""
    await pg_manager.acquire_advisory_lock()
    try:
        async with pg_manager.get_async_session_context() as db:
            await RuntimeLeaderRepository(db).claim_leader_term(
                cluster_id="local", instance_id="i-1", now_ms=1
            )
            await db.commit()
        elector = LeaderElector(cluster_id="another-cluster")
        with pytest.raises(ClusterIdMismatchError):
            await elector.acquire()
    finally:
        await pg_manager.close()


@requires_live_postgres
@pytest.mark.asyncio
async def test_migration_lock_serializes_and_releases() -> None:
    """migration lock 与执行锁互不干扰；释放后可重取。"""
    await pg_manager.acquire_migration_lock()
    try:
        # 同一 manager 重复获取幂等
        await pg_manager.acquire_migration_lock(timeout_seconds=0.5)
    finally:
        await pg_manager.release_migration_lock()
    # 释放后可再次获取
    await pg_manager.acquire_migration_lock(timeout_seconds=0.5)
    await pg_manager.release_migration_lock()
    # 执行锁与 migration lock 是不同 key，互不阻塞
    await pg_manager.acquire_advisory_lock()
    try:
        await pg_manager.acquire_migration_lock(timeout_seconds=0.5)
        await pg_manager.release_migration_lock()
    finally:
        await pg_manager.close()
