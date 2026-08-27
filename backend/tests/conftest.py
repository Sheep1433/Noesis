"""Pytest host wiring for observability integration tests."""

from __future__ import annotations

import os

import pytest

# 单进程测试默认 memory run bus（生产必填、不提供默认；测试显式声明）
os.environ.setdefault("NOESIS_RUN_BUS_BACKEND", "memory")


@pytest.fixture(scope="session", autouse=True)
def _wire_runtime_observability() -> None:
    from server.wiring import wire_runtime_observability

    wire_runtime_observability()
