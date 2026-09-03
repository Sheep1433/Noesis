"""per-run 投递内核：主链路 RunManager 与子 Agent executor 共用的投递语义单点。

统一语义（两侧一份实现）：
- 单调 sequence 分配（从 1 起；transient 不占号、不参与缺口判定）
- 有界重放缓存（事件数与字节数双上限，保留最新、淘汰最旧）
- 连续性重放：缓存能从 ``after_sequence+1`` 连续补齐才重放，否则要求快照恢复
- transient 旁路：只投在线订阅者

锁契约：内核是**被动数据结构**，无自有锁，全部方法在持有方锁内调用
（RunManager 的 RunHandle.lock=asyncio、executor 的 threading.Lock）——
「sequence 与 projection 原子一致」由持有方锁纪律保证。fanout 的投递
机制（同 loop ``put_nowait`` / 跨 loop ``call_soon_threadsafe`` / 慢订阅者
处置）由持有方注入，内核只负责语义。

按 run 持有内核实例的注册表（RunManager 的 ``_runs`` / executor 的
``_RUN_DELIVERY``）是实例存储，不是第二套投递语义实现。
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class SequencedPayload:
    """executor 侧的信封：任意 payload dict/对象 + sequence 与体量估算。

    RunManager 侧直接用自己的 ``SequencedRunEvent``（天然满足
    ``sequence`` / ``estimated_bytes`` 鸭子类型），不经本信封。
    """

    sequence: int
    payload: Any
    estimated_bytes: int = 0

    def __post_init__(self) -> None:
        if self.estimated_bytes <= 0:
            try:
                self.estimated_bytes = len(json.dumps(self.payload, ensure_ascii=False).encode("utf-8")) + 64
            except (TypeError, ValueError):
                self.estimated_bytes = 256


def _envelope_bytes(envelope: Any) -> int:
    size = getattr(envelope, "estimated_bytes", 0)
    return int(size) if size > 0 else 256


class DeliveryCore:
    """per-run 投递内核（被动数据结构，持有方锁内调用）。"""

    def __init__(self, *, max_buffer_events: int, max_buffer_bytes: int) -> None:
        self.next_sequence = 1
        self.buffer: collections.deque[Any] = collections.deque()
        self.buffer_bytes = 0
        self.max_buffer_events = max_buffer_events
        self.max_buffer_bytes = max_buffer_bytes
        # 订阅者条目为持有方形态（executor：(loop, queue, user_id)）；内核只遍历
        self.subscribers: list[Any] = []

    # ---- 持久事件：占号、进缓存 ----

    def assign_sequence(self) -> int:
        sequence = self.next_sequence
        self.next_sequence += 1
        return sequence

    def commit(self, envelope: Any) -> None:
        """持久信封入缓存并对齐序号（``next_sequence = max(当前, envelope.sequence+1)``）。

        调用方先 peek ``next_sequence`` 构造信封、通过限额检查后 commit；
        终态重试路径的序号回退由调用方先行设置 ``next_sequence``。
        """
        self.next_sequence = max(self.next_sequence, getattr(envelope, "sequence", 0) + 1)
        self.buffer.append(envelope)
        self.buffer_bytes += _envelope_bytes(envelope)
        self.trim()

    def append(self, envelope: Any) -> None:
        """缓存带 sequence 的信封，不推进序号（重放/重试追加场景）。"""
        self.buffer.append(envelope)
        self.buffer_bytes += _envelope_bytes(envelope)
        self.trim()

    def clear(self) -> None:
        self.buffer.clear()
        self.buffer_bytes = 0

    def trim(self) -> None:
        while self.buffer and (
            len(self.buffer) > self.max_buffer_events
            or self.buffer_bytes > self.max_buffer_bytes
        ):
            removed = self.buffer.popleft()
            self.buffer_bytes -= _envelope_bytes(removed)

    # ---- 重放 ----

    def replay_after(self, after_sequence: int) -> tuple[tuple[Any, ...], bool]:
        """连续性重放：``(replay, snapshot_required)``。

        - ``after_sequence <= 0``：首连， ``((), False)``——由快照恢复，不重放；
        - 缓存可从 ``after_sequence+1`` 连续补齐（或无可补）：``(buffered, False)``；
        - 缓存断档（早于可补窗口）：``((), True)``——调用方 SHALL 只发快照，
          SHALL NOT 假装已连续补齐。
        """
        if after_sequence <= 0:
            return ((), False)
        buffered = tuple(item for item in self.buffer if getattr(item, "sequence", 0) > after_sequence)
        if buffered and buffered[0].sequence != after_sequence + 1:
            return ((), True)
        return (buffered, False)

    # ---- transient 旁路 ----

    def publish_transient(self, payload: Any, put: Callable[[Any, Any], None]) -> None:
        """只投在线订阅者：不占号、不进缓存、不参与重放。"""
        for subscriber in tuple(self.subscribers):
            put(subscriber, payload)
