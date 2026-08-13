"""命令结果结构。

``dispatch`` 返回结构化 ``CommandResult`` 而非裸 ``str``：各通道能力不同
（Telegram 4000 字 + MarkdownV2 转义、Web 富文本 SSE、CLI 终端），dispatch 产出
通道无关的结构化结果，再由各通道 ``project_outbound`` / ``render`` 投影。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RequestRewrite:
    """技能快捷命令（D 类）：命中后改写为一次 Agent run，而非直接回复文本。

    通道以 ``query`` + ``enabled_skills`` 走 ``SuperAgent``，不另起执行路径。
    首批 A 类不使用本结构，仅预留扩展点。
    """

    query: str
    enabled_skills: list[str] = field(default_factory=list)


@dataclass
class CommandResult:
    """``dispatch`` 的返回值。

    三种形态：
    - 直接回复：``handled=True, text="..."`` → 通道投影后回复，不启动 Agent、不落库。
    - 放行改写（D 类）：``handled=True, rewrite_request=...`` → 以改写请求走 Agent run。
    - 未命中：``handled=False`` → 原路径放行（进 Agent / mention 解析）。
    """

    handled: bool
    text: str = ""
    rewrite_request: Optional[RequestRewrite] = None
