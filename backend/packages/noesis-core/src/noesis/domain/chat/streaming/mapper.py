"""RuntimeEventMapper：LangGraph/LangChain raw event → typed RunEvent 的唯一映射入口。

它是无状态模块（Bridge 实例有状态，mapper 本身不持有状态），不做 plugin registry
或深类层级。Web、Channel、cron 和 eval 中属于本 change 范围的 Agent Run 共用此 mapper。

当前实现复用 LangGraphSseBridge 的状态机进行 raw event 提取，再把产出的 SSE 行
解析为 typed RunEvent。这保证了与现网契约一致，同时把映射入口收敛到单一模块。
后续可逐步把 Bridge 的 _emit_* 方法改为直接产出 RunEvent，消除内部 SSE 序列化往返。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from noesis.domain.chat.delivery.events import RunEvent
from noesis.domain.chat.delivery.sse import parse_sse_line_to_event
from noesis.domain.chat.message_builder import AssistantMessageBuilder
from noesis.domain.chat.streaming.langgraph_sse import LangGraphSseBridge


class RuntimeEventMapper:
    """raw runtime event → typed RunEvent 的唯一映射入口。

    持有一个 LangGraphSseBridge 实例（有状态），复用其状态提取逻辑。
    每条 raw event 经 bridge.process_item 产出 SSE 行，再解析为 typed RunEvent。
    """

    def __init__(self, bridge: LangGraphSseBridge) -> None:
        self.bridge = bridge

    def map_item(
        self,
        item: Dict[str, Any],
        builder: Optional[AssistantMessageBuilder],
        ctx: Dict[str, Any],
    ) -> List[RunEvent]:
        """单条 raw event → 多条 typed RunEvent。"""
        lines = self.bridge.process_item(item, builder, ctx)
        return self._lines_to_events(lines)

    def finalize(self, *, finish_reason: Optional[str] = None) -> List[RunEvent]:
        """流结束：产出 finish + StreamDone。"""
        lines = self.bridge.finalize(finish_reason=finish_reason)
        return self._lines_to_events(lines)

    @staticmethod
    def _lines_to_events(lines: List[str]) -> List[RunEvent]:
        events: List[RunEvent] = []
        for line in lines:
            events.extend(parse_sse_line_to_event(line))
        return events
