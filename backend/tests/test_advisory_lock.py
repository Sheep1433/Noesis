"""Phase 7 单 active backend advisory lock 测试。

验证 PostgreSQL advisory lock 强制单 active backend：
- 第一个连接获取 lock 成功
- 第二个连接获取同一 lock 失败（fail-fast）
- 释放后可重新获取
"""

from __future__ import annotations

import os
import pytest

from noesis.storage.postgres.manager import (
    _NOESIS_ADVISORY_LOCK_KEY1,
    _NOESIS_ADVISORY_LOCK_KEY2,
    pg_manager,
)


def _can_connect_postgres() -> bool:
    """检查是否能连接 PostgreSQL（跳过纯单元测试环境）。"""
    import asyncio
    import asyncpg
    from urllib.parse import quote_plus
    from noesis.config.env import DataBaseConfig

    dsn = (
        f"postgresql://{DataBaseConfig.postgres_user}:"
        f"{quote_plus(DataBaseConfig.postgres_password)}@"
        f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/"
        f"{DataBaseConfig.postgres_database}"
    )

    async def _try():
        try:
            conn = await asyncpg.connect(dsn, timeout=2)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.new_event_loop().run_until_complete(_try())
    except Exception:
        return False


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("NOESIS_LIVE_POSTGRES_TEST") != "1" or not _can_connect_postgres(),
    reason="设置 NOESIS_LIVE_POSTGRES_TEST=1 后运行真实 PostgreSQL 隔离测试",
)
async def test_advisory_lock_excludes_second_instance() -> None:
    """第二个连接无法获取同一 advisory lock。"""
    import asyncpg
    from urllib.parse import quote_plus
    from noesis.config.env import DataBaseConfig

    dsn = (
        f"postgresql://{DataBaseConfig.postgres_user}:"
        f"{quote_plus(DataBaseConfig.postgres_password)}@"
        f"{DataBaseConfig.postgres_host}:{DataBaseConfig.postgres_port}/"
        f"{DataBaseConfig.postgres_database}"
    )

    # 第一个连接获取 lock
    conn1 = await asyncpg.connect(dsn)
    acquired1 = await conn1.fetchval(
        "SELECT pg_try_advisory_lock($1, $2)",
        _NOESIS_ADVISORY_LOCK_KEY1,
        _NOESIS_ADVISORY_LOCK_KEY2,
    )
    assert acquired1 is True

    # 第二个连接无法获取同一 lock
    conn2 = await asyncpg.connect(dsn)
    acquired2 = await conn2.fetchval(
        "SELECT pg_try_advisory_lock($1, $2)",
        _NOESIS_ADVISORY_LOCK_KEY1,
        _NOESIS_ADVISORY_LOCK_KEY2,
    )
    assert acquired2 is False

    # 释放后可重新获取
    await conn1.fetchval(
        "SELECT pg_advisory_unlock($1, $2)",
        _NOESIS_ADVISORY_LOCK_KEY1,
        _NOESIS_ADVISORY_LOCK_KEY2,
    )
    acquired3 = await conn2.fetchval(
        "SELECT pg_try_advisory_lock($1, $2)",
        _NOESIS_ADVISORY_LOCK_KEY1,
        _NOESIS_ADVISORY_LOCK_KEY2,
    )
    assert acquired3 is True

    await conn2.fetchval(
        "SELECT pg_advisory_unlock($1, $2)",
        _NOESIS_ADVISORY_LOCK_KEY1,
        _NOESIS_ADVISORY_LOCK_KEY2,
    )
    await conn1.close()
    await conn2.close()


def test_advisory_lock_key_is_fixed() -> None:
    """advisory lock key 是固定值，所有实例使用同一 key。"""
    assert _NOESIS_ADVISORY_LOCK_KEY1 == 0x4E6F6573
    assert _NOESIS_ADVISORY_LOCK_KEY2 == 0x69735F61


@pytest.mark.asyncio
async def test_monitor_marks_instance_not_ready_when_lock_connection_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noesis.storage.postgres.manager import PostgresManager

    manager = PostgresManager()
    connection = type(
        "BrokenConnection",
        (),
        {"fetchval": lambda self, *_args: _raise_connection_error()},
    )()
    manager._advisory_lock_conn = connection
    manager._advisory_lock_ready = True

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr("noesis.storage.postgres.manager.asyncio.sleep", no_wait)
    await manager.monitor_advisory_lock()

    assert manager.advisory_lock_ready is False
    assert manager._advisory_lock_conn is None


async def _raise_connection_error():
    raise ConnectionError("lock connection lost")
