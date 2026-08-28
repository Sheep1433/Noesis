"""推理档位（reasoning_effort）核心模块回归测试。"""

from __future__ import annotations

import pytest

from noesis.llm.reasoning import (
    REASONING_LEVELS,
    clear_request_reasoning_effort,
    get_request_reasoning_effort,
    set_request_reasoning_effort,
    to_wire_reasoning_effort,
)


@pytest.mark.parametrize(
    ("level", "wire"),
    [
        ("off", "none"),
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("max", "max"),
    ],
)
def test_wire_mapping(level: str, wire: str) -> None:
    assert to_wire_reasoning_effort(level) == wire


def test_wire_mapping_rejects_unknown_level() -> None:
    with pytest.raises(ValueError):
        to_wire_reasoning_effort("xhigh")


def test_request_reasoning_effort_contextvar_roundtrip() -> None:
    clear_request_reasoning_effort()
    assert get_request_reasoning_effort() is None
    set_request_reasoning_effort("high")
    assert get_request_reasoning_effort() == "high"
    clear_request_reasoning_effort()
    assert get_request_reasoning_effort() is None
