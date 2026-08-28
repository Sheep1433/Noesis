"""推理档位（reasoning_effort）：统一档位枚举与当前 Run 的请求侧透传。

设计（openspec: reasoning-effort）：
- 档位轴 ``off/low/medium/high/max``，wire 映射 ``off -> "none"``，其余原值；
  「自动」= 键缺失 = 不传参数（provider 默认行为，现有部署零变化）。
- 仅做顶层 ``reasoning_effort`` 通用透传，不做 per-vendor 参数映射
  （Qwen enable_thinking / DeepSeek thinking 对象 / Claude budget_tokens 均不支持）。
- 当前 Run 的档位经 ContextVar 传播（仿 runtime_snapshot 先例），
  不在 run_agent / create_noesis_agent 全链路加签名参数。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Iterable

REASONING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high", "max")

# UI「自动」不进枚举：它表示键缺失。wire 上 off 映射为 OpenAI 规范的 none。
_WIRE_REASONING_EFFORT: dict[str, str] = {"off": "none"}


def to_wire_reasoning_effort(level: str) -> str:
    """档位 → wire 值：off→none，其余原值。非法档位抛 ValueError（调用方已校验）。"""
    if level not in REASONING_LEVELS:
        raise ValueError(f"非法推理档位: {level!r}，仅允许 {'/'.join(REASONING_LEVELS)}")
    return _WIRE_REASONING_EFFORT.get(level, level)


def normalize_reasoning_levels(raw: Any) -> tuple[str, ...]:
    """能力声明归一化：过滤非法、按枚举序去重。输入 None/str/list 均可。"""
    if raw is None:
        return ()
    if isinstance(raw, str):
        items: Iterable[str] = (part.strip() for part in raw.split(","))
    elif isinstance(raw, (list, tuple, set)):
        items = (str(part).strip() for part in raw)
    else:
        return ()
    valid = {item for item in items if item in REASONING_LEVELS}
    return tuple(level for level in REASONING_LEVELS if level in valid)


_request_reasoning_effort: ContextVar[str | None] = ContextVar(
    "noesis_request_reasoning_effort", default=None
)


def set_request_reasoning_effort(value: str | None) -> None:
    """在 exec_query 入口设置本 Run 的档位；子任务经 create_task 自动继承。"""
    _request_reasoning_effort.set(value)


def get_request_reasoning_effort() -> str | None:
    return _request_reasoning_effort.get()


def clear_request_reasoning_effort() -> None:
    _request_reasoning_effort.set(None)


__all__ = [
    "REASONING_LEVELS",
    "to_wire_reasoning_effort",
    "normalize_reasoning_levels",
    "set_request_reasoning_effort",
    "get_request_reasoning_effort",
    "clear_request_reasoning_effort",
]
