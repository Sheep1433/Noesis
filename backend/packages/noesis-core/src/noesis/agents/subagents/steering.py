"""后台子 Agent 的中途策略调整（steering）通道。

指令按 task_id 键控入队（有界，溢出丢最旧），``SteeringMiddleware`` 在子
Agent 下一次模型调用边界 drain 并注入为追加 HumanMessage——语义对齐
Claude Code SendMessage 的「下一个回合边界生效」。
"""

from __future__ import annotations

import threading
from collections import deque

MAX_PENDING_PER_TASK = 10

_QUEUE_LOCK = threading.Lock()
_QUEUES: dict[str, deque[str]] = {}


def put(task_id: str, message: str) -> None:
    """入队一条调整指令（终态校验由调用方 executor 完成）。"""
    text = message.strip()
    if not text:
        raise ValueError("调整指令不能为空")
    with _QUEUE_LOCK:
        queue = _QUEUES.setdefault(task_id, deque(maxlen=MAX_PENDING_PER_TASK))
        queue.append(text)


def drain(task_id: str) -> list[str]:
    """取出并清空该任务的待注入指令（模型调用边界消费）。"""
    with _QUEUE_LOCK:
        queue = _QUEUES.get(task_id)
        if not queue:
            return []
        messages = list(queue)
        queue.clear()
        return messages


def clear(task_id: str) -> None:
    """任务终态清理（cancel 路径调用）。"""
    with _QUEUE_LOCK:
        _QUEUES.pop(task_id, None)


__all__ = ["MAX_PENDING_PER_TASK", "clear", "drain", "put"]
