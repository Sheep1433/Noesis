"""Pytest: wire harness platform deps for tests that touch attachment/KB tools."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _wire_harness_deps() -> None:
    from noesis_server.wiring import wire_harness_platform_deps

    wire_harness_platform_deps()
