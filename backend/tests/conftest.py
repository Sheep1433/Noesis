"""Pytest host wiring for observability integration tests."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _wire_runtime_observability() -> None:
    from server.wiring import wire_runtime_observability

    wire_runtime_observability()
