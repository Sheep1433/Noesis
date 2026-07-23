"""ChannelAdapter SPI、Registry、Binding（运行时；配置存 settings）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from domain.chat.delivery.events import RunEvent


@dataclass
class ChannelCapabilities:
    streaming_edit: bool = False
    max_text_len: int = 4000
    markdown: bool = True
    mirror_tools: bool = False


@dataclass
class InboundMessage:
    channel_type: str
    external_chat_id: str
    text: str
    external_message_id: Optional[str] = None
    thread_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelBinding:
    user_id: str
    channel_type: str
    external_chat_id: str
    session_id: str
    thread_id: Optional[str] = None


@runtime_checkable
class ChannelAdapter(Protocol):
    channel_type: str
    capabilities: ChannelCapabilities

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def normalize_inbound(self, raw: Dict[str, Any]) -> Optional[InboundMessage]: ...

    async def project_outbound(self, events: List[RunEvent]) -> None: ...


class ChannelRegistry:
    def __init__(self) -> None:
        self._adapters: Dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        self._adapters[adapter.channel_type] = adapter

    def get(self, channel_type: str) -> Optional[ChannelAdapter]:
        return self._adapters.get(channel_type)

    def require(self, channel_type: str) -> ChannelAdapter:
        ad = self.get(channel_type)
        if ad is None:
            raise KeyError(f"no ChannelAdapter for type={channel_type}")
        return ad

    def types(self) -> List[str]:
        return sorted(self._adapters.keys())


class ChannelBindingStore:
    """进程内绑定表；持久化对接 settings 后可替换实现。"""

    def __init__(self) -> None:
        self._by_external: Dict[tuple[str, str, str], ChannelBinding] = {}

    @staticmethod
    def _key(
        channel_type: str,
        external_chat_id: str,
        thread_id: Optional[str] = None,
    ) -> tuple[str, str, str]:
        return (channel_type, external_chat_id, thread_id or "")

    def put(self, binding: ChannelBinding) -> None:
        self._by_external[
            self._key(binding.channel_type, binding.external_chat_id, binding.thread_id)
        ] = binding

    def resolve(
        self,
        channel_type: str,
        external_chat_id: str,
        thread_id: Optional[str] = None,
    ) -> Optional[ChannelBinding]:
        return self._by_external.get(self._key(channel_type, external_chat_id, thread_id))

    def clear(self) -> None:
        self._by_external.clear()

    def clear_user(self, user_id: str) -> None:
        uid = str(user_id)
        stale = [k for k, b in self._by_external.items() if b.user_id == uid]
        for k in stale:
            self._by_external.pop(k, None)


@dataclass
class StubChannelAdapter:
    """参考/测试用 stub；不真正收发。"""

    channel_type: str
    capabilities: ChannelCapabilities
    outbound_batches: List[List[RunEvent]] = field(default_factory=list)
    started: bool = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def normalize_inbound(self, raw: Dict[str, Any]) -> Optional[InboundMessage]:
        text = str(raw.get("text") or "")
        chat_id = str(raw.get("chat_id") or "")
        if not chat_id:
            return None
        return InboundMessage(
            channel_type=self.channel_type,
            external_chat_id=chat_id,
            text=text,
            external_message_id=raw.get("message_id"),
            raw=raw,
        )

    async def project_outbound(self, events: List[RunEvent]) -> None:
        if not self.capabilities.streaming_edit:
            # final-only：仅保留完成类，由调用方过滤亦可
            self.outbound_batches.append(list(events))
        else:
            self.outbound_batches.append(list(events))


def build_default_registry() -> ChannelRegistry:
    from domain.chat.delivery.telegram.adapter import TelegramChannelAdapter

    reg = ChannelRegistry()
    reg.register(TelegramChannelAdapter())
    reg.register(
        StubChannelAdapter(
            channel_type="wechat",
            capabilities=ChannelCapabilities(streaming_edit=False, max_text_len=2048),
        )
    )
    return reg


channel_registry = build_default_registry()
channel_bindings = ChannelBindingStore()


@dataclass
class InboundRouteResult:
    ok: bool
    binding: Optional[ChannelBinding] = None
    reject_reason: str = ""


def route_inbound(
    message: InboundMessage,
    *,
    store: Optional[ChannelBindingStore] = None,
) -> InboundRouteResult:
    """未配对发送方 SHALL NOT 触发特权 Agent。"""
    st = store or channel_bindings
    binding = st.resolve(
        message.channel_type,
        message.external_chat_id,
        message.thread_id,
    )
    if binding is None:
        return InboundRouteResult(
            ok=False,
            reject_reason="unpaired",
        )
    return InboundRouteResult(ok=True, binding=binding)


def project_for_capabilities(
    events: List[RunEvent],
    caps: ChannelCapabilities,
) -> List[RunEvent]:
    """按 capabilities 过滤出站事件（单测/stub 用）。"""
    from domain.chat.delivery.events import RunCompleted, WireFrame

    if caps.streaming_edit:
        return list(events)
    out: List[RunEvent] = []
    for ev in events:
        if isinstance(ev, RunCompleted):
            out.append(ev)
        elif isinstance(ev, WireFrame) and ev.event == "finish":
            out.append(ev)
    return out
