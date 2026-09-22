"""本地信令总线 ↔ Run bus 的桥（enable-distributed-sse-pubsub task 4.7）。

redis 模式下装配（memory 模式不装配，进程内行为不变）：

- **发布向**：本地信令总线 publish 时同步上 bus（``origin`` 标记本实例），
  经主 loop fire-and-forget——hint 语义允许丢，失败只记 debug；
- **订阅向**：本地某 (scope, key) 出现首个订阅者时建立一份远端订阅泵
  （多本地订阅者复用），最后一个离开时释放——与 Run hub 同模式；
- **回声抑制**：泵丢弃 origin 为本实例的信令（自己发布的那份已走本地
  fan-out，远端回环不得二次投递）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from noesis.runtime.logging import logger


def schedule_bus_coro(coro, *, name: str) -> None:
    """把 bus 发布协程调度到正确的 loop（调用方可能在隔离 loop 线程）。

    主 loop 已捕获且存活时一律经主 loop（redis 客户端连接池绑定单 loop，
    隔离 loop 线程不得跨 loop 使用客户端）；未捕获或已关闭（单测残留/CLI）
    退回当前 loop；无任何 loop 则丢弃（hint 语义允许丢）。
    """
    from noesis.runtime.main_loop import current_main_loop, run_on_main_loop

    main = current_main_loop()
    if main is not None and not main.is_closed():
        run_on_main_loop(coro, name=name)
        return
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()


class SignalBridge:
    """装配到各本地信令总线上的跨进程桥。"""

    def __init__(self, *, bus: Any, origin: str) -> None:
        self._bus = bus
        self._origin = origin
        self._pumps: dict[tuple[str, str], "_SignalPump"] = {}

    @property
    def origin(self) -> str:
        return self._origin

    @property
    def pump_count(self) -> int:
        return len(self._pumps)

    def publish(self, scope: str, key: str, payload: dict) -> None:
        """非阻塞上 bus：loop 路由见 schedule_bus_coro。"""

        async def _go() -> None:
            try:
                await self._bus.publish_signal(scope, key, payload, origin=self._origin)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "signal bridge publish dropped scope={} key={}", scope, key
                )

        schedule_bus_coro(_go(), name=f"signal-bridge-publish:{scope}:{key}")

    def ensure_pump(
        self, scope: str, key: str, deliver: Callable[[dict], None]
    ) -> None:
        """本地首个订阅者到达：建远端订阅泵；deliver 把远端信令投回本地总线。"""
        channel = (scope, key)
        if channel in self._pumps:
            return
        pump = _SignalPump(
            bus=self._bus, scope=scope, key=key, origin=self._origin, deliver=deliver
        )
        self._pumps[channel] = pump
        pump.start()

    def release_pump(self, scope: str, key: str) -> None:
        """本地最后一个订阅者离开：释放远端订阅。"""
        pump = self._pumps.pop((scope, key), None)
        if pump is not None:
            pump.stop()

    async def close(self) -> None:
        for (scope, key) in list(self._pumps):
            self.release_pump(scope, key)


class _SignalPump:
    """单 (scope, key) 的远端订阅泵：远端信令 → 本地 deliver（回声抑制）。"""

    def __init__(
        self,
        *,
        bus: Any,
        scope: str,
        key: str,
        origin: str,
        deliver: Callable[[dict], None],
    ) -> None:
        self._bus = bus
        self._scope = scope
        self._key = key
        self._origin = origin
        self._deliver = deliver
        self._task: asyncio.Task | None = None
        self._subscription: Any = None

    def start(self) -> None:
        self._task = asyncio.create_task(
            self._run(), name=f"signal-pump:{self._scope}:{self._key}"
        )

    def stop(self) -> None:
        task = self._task
        self._task = None

        async def _close() -> None:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if self._subscription is not None:
                try:
                    await self._subscription.close()
                except Exception:  # noqa: BLE001
                    pass
                self._subscription = None

        try:
            asyncio.get_running_loop().create_task(_close())
        except RuntimeError:
            pass

    async def _run(self) -> None:
        try:
            self._subscription = await self._bus.subscribe_signals(
                self._scope, self._key
            )
            await self._subscription.ready()
            async for message in self._subscription:
                if message.get("origin") == self._origin:
                    continue  # 自己发布的回声：本地 fan-out 已投递
                payload = message.get("payload")
                if isinstance(payload, dict):
                    self._deliver(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.opt(exception=True).error(
                "signal pump 异常退出 scope={} key={}", self._scope, self._key
            )


__all__ = ["SignalBridge"]
