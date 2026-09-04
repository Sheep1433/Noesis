"""HITL 决策载荷归一化：pydantic 对象 → 纯 dict + 拒绝默认文案。

主/子 Agent 两条 resume 路径共用：executor 与 langchain HITL 中间件都要求
纯 dict（中间件按下标取值，pydantic 对象会 TypeError）；拒绝不带 message 时
统一补默认文案——否则不同前端入口会产生中文/英文两种拒绝文案。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

REJECT_DEFAULT_MESSAGE = "用户拒绝了该操作"


def normalize_hitl_decisions(decisions: list[Any]) -> list[dict[str, Any]]:
    """决策列表 → 纯 dict 列表；reject 缺 message 时补统一默认文案。"""
    payloads: list[dict[str, Any]] = []
    for item in decisions:
        payload = item.model_dump(exclude_none=True) if isinstance(item, BaseModel) else dict(item)
        if payload.get("type") == "reject" and not payload.get("message"):
            payload["message"] = REJECT_DEFAULT_MESSAGE
        payloads.append(payload)
    return payloads
