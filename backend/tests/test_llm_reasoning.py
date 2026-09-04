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


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_wire_mapping_is_identity(level: str) -> None:
    """通用三档：wire 值即档位名（OpenAI chat/completions 顶层 reasoning_effort）。"""
    assert to_wire_reasoning_effort(level) == level


@pytest.mark.parametrize("level", ["off", "max", "none", "xhigh", ""])
def test_wire_mapping_rejects_non_universal_level(level: str) -> None:
    """非通用档位（off/max 等）已收窄掉，进档位通道直接拒绝。"""
    with pytest.raises(ValueError):
        to_wire_reasoning_effort(level)


def test_levels_are_universal_three() -> None:
    assert REASONING_LEVELS == ("low", "medium", "high")


def test_request_reasoning_effort_contextvar_roundtrip() -> None:
    clear_request_reasoning_effort()
    assert get_request_reasoning_effort() is None
    set_request_reasoning_effort("high")
    assert get_request_reasoning_effort() == "high"
    clear_request_reasoning_effort()
    assert get_request_reasoning_effort() is None
