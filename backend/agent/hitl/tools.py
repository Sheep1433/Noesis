"""ask_user 工具与 SuperAgent ``interrupt_on`` 工厂。"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.hitl.policy import execute_when, memory_write_when

_APPROVE_REJECT: list[str] = ["approve", "reject"]
_RESPOND_ONLY: list[str] = ["respond"]


class AskUserInput(BaseModel):
    question: str = Field(description="向用户提出的澄清问题")
    options: list[str] | None = Field(
        default=None,
        description="优先提供 2–5 个互斥可选答案；无法穷举时省略以使用自由文本",
    )


def _ask_user_impl(question: str, options: list[str] | None = None) -> str:
    """工具体为 no-op；真实回答仅经 HITL ``respond`` 注入 ToolMessage。"""
    _ = options
    return f"[ask_user pending] {question}"


ask_user_tool = StructuredTool.from_function(
    func=_ask_user_impl,
    name="ask_user",
    description=(
        "向用户澄清任务执行中缺少的关键信息。"
        "优先提供 options（2–5 个互斥选项）便于用户点选；仅当无法穷举选项时用自由文本。"
        "可在同一轮并行多次调用以提出多个独立问题。"
        "仅在任务已启动、已进入工具循环后使用；"
        "任务入口寒暄或意图不明时不要调用，应纯文本追问。"
    ),
    args_schema=AskUserInput,
)


def build_interrupt_on(*, session_id: str | None = None) -> dict[str, InterruptOnConfig]:
    """组装 SuperAgent（及 task-worker）的 ``interrupt_on``。"""

    def _execute_when(req: Any) -> bool:
        return execute_when(req, session_id=session_id)

    return {
        "execute": {
            "allowed_decisions": list(_APPROVE_REJECT),
            "description": "危险 Shell 命令需要用户确认后执行",
            "when": _execute_when,
        },
        "write_file": {
            "allowed_decisions": list(_APPROVE_REJECT),
            "description": "写入用户记忆（/memory/）需要确认",
            "when": memory_write_when,
        },
        "edit_file": {
            "allowed_decisions": list(_APPROVE_REJECT),
            "description": "编辑用户记忆（/memory/）需要确认",
            "when": memory_write_when,
        },
        "ask_user": {
            "allowed_decisions": list(_RESPOND_ONLY),
            "description": "等待用户回答澄清问题",
        },
    }
