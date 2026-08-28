"""推理档位（reasoning_effort）：统一档位枚举与当前 Run 的请求侧透传。

- 档位轴 ``low/medium/high``（各家通用三档）；wire 值即档位名；
  「自动」= 键缺失 = 不传参数（provider 默认行为）。
- 仅做顶层 ``reasoning_effort`` 通用透传（OpenAI 协议族）；入口只对
  已知支持的模型显示（前端按模型名规则），qwen/anthropic 走专有
  参数体系、构造层跳过注入。
- 当前 Run 的档位经 ContextVar 传播（仿 runtime_snapshot 先例），
  不在 run_agent / create_noesis_agent 全链路加签名参数。
"""

from __future__ import annotations

from contextvars import ContextVar

REASONING_LEVELS: tuple[str, ...] = ("low", "medium", "high")


def to_wire_reasoning_effort(level: str) -> str:
    """档位 → wire 值：三档通用值即档位名本身。非法档位抛 ValueError。"""
    if level not in REASONING_LEVELS:
        raise ValueError(f"非法推理档位: {level!r}，仅允许 {'/'.join(REASONING_LEVELS)}")
    return level


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
    "set_request_reasoning_effort",
    "get_request_reasoning_effort",
    "clear_request_reasoning_effort",
]
