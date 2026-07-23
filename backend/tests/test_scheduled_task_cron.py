"""定时任务 cron 校验与 CRUD（内存 SQLite 不可用 FOR UPDATE；用服务层单测）。"""
from __future__ import annotations

import pytest

from services.scheduled_task_service import (
    compute_next_run_ms,
    validate_cron_expr,
)


def test_validate_cron_ok() -> None:
    validate_cron_expr("0 9 * * *", "Asia/Shanghai")


def test_validate_cron_bad() -> None:
    with pytest.raises(ValueError, match="非法 cron"):
        validate_cron_expr("not a cron", "Asia/Shanghai")


def test_validate_timezone_bad() -> None:
    with pytest.raises(ValueError, match="timezone"):
        validate_cron_expr("0 9 * * *", "Not/AZone")


def test_next_run_ms_monotonic() -> None:
    a = compute_next_run_ms("*/5 * * * *", "UTC", after_ms=1_700_000_000_000)
    b = compute_next_run_ms("*/5 * * * *", "UTC", after_ms=a)
    assert b > a
