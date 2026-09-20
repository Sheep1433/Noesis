"""会话级与 run 级事件订阅（SSE push，替代前端轮询）。

发布发生在隔离线程，经 call_soon_threadsafe 跨 loop 投递到订阅者队列。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

from noesis.chat.runs import SubscriptionLimitExceeded
from noesis.chat.runs.delivery_bus import DeliveryCore, SequencedPayload
from noesis.config.env import StreamConfig

from noesis.agents.background.jobs.state import BackgroundTask

# ---------------------------------------------------------------------------
# 会话级事件订阅（SSE push，替代前端轮询）：executor 在隔离线程发布，
# 经 call_soon_threadsafe 跨 loop 投递到订阅者的 asyncio.Queue
# ---------------------------------------------------------------------------

_BGSub = tuple[asyncio.AbstractEventLoop, asyncio.Queue, str]  # (loop, queue, user_id)
_SUBSCRIBERS: dict[str, list[_BGSub]] = {}
_SUBSCRIBERS_LOCK = threading.Lock()
# 跨进程信令桥（task 4.7，redis 模式装配）：bg-tasks:{session_id} 通道——
# 任务 started/progress/terminal、child-session 目录刷新与 continuation 提示
_SIGNAL_BRIDGE = None


def configure_bg_signal_bridge(bridge) -> None:
    """装配/拆卸跨进程信令桥（run_service 按运行模式装配；测试复位传 None）。"""
    global _SIGNAL_BRIDGE
    _SIGNAL_BRIDGE = bridge


# 子会话 RunEvent 跨进程发布桥（task 4.9，redis 模式装配）：leader 侧把
# 子会话 run 事件按 Run bus envelope（run_id=子会话 Run、sequence=投影
# sequence、owner_term）发布，follower 侧由 Run hub 订阅恢复
_RUN_EVENT_BUS = None
_RUN_EVENT_TOKEN = None


def configure_run_event_bridge(bus, token_provider) -> None:
    """装配/拆卸子会话 RunEvent 发布桥（main.py leader 选举后装配）。"""
    global _RUN_EVENT_BUS, _RUN_EVENT_TOKEN
    _RUN_EVENT_BUS = bus
    _RUN_EVENT_TOKEN = token_provider


def _publish_run_event_to_bus(payload: dict[str, Any]) -> None:
    """子会话事件上 Run bus（fire-and-forget；发布方在隔离 loop 线程）。

    at-most-once 实时面：失败丢弃，follower 由 hub 的 snapshot 对账恢复。
    """
    if _RUN_EVENT_BUS is None or _RUN_EVENT_TOKEN is None:
        return
    from noesis.chat.runs.bus import RUN_BUS_SCHEMA_VERSION, RunEventEnvelope
    from noesis.chat.runs.signal_bridge import schedule_bus_coro

    token = _RUN_EVENT_TOKEN()
    if token is None or not getattr(token, "valid", False):
        return
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        return
    envelope = RunEventEnvelope(
        schema_version=RUN_BUS_SCHEMA_VERSION,
        run_id=run_id,
        owner_instance_id=str(token.instance_id),
        owner_term=int(token.term),
        sequence=int(payload.get("sequence") or 0),
        attempt_id=1,
        event_type=str(payload.get("type") or "run.event"),
        payload=payload,
    )
    bus = _RUN_EVENT_BUS

    async def _go() -> None:
        try:
            await bus.publish_run_events(run_id, [envelope])
        except Exception:  # noqa: BLE001
            pass  # hint 级丢弃：follower 由 snapshot 对账兜底

    schedule_bus_coro(_go(), name=f"bg-run-event:{run_id}")
# 子会话 run 事件投递：统一投递内核实例注册表（按 run_id 持有，语义实现
# 在 DeliveryCore 单点）。缓存上限与订阅配额与主链路同一份 StreamConfig。
_RUN_DELIVERY: dict[str, DeliveryCore] = {}
_RUN_DELIVERY_LOCK = threading.Lock()

def _delivery_core(run_id: str) -> DeliveryCore:
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
        if core is None:
            core = DeliveryCore(
                max_buffer_events=StreamConfig.run_event_buffer_max_events,
                max_buffer_bytes=StreamConfig.run_event_buffer_max_bytes,
            )
            _RUN_DELIVERY[run_id] = core
        return core

def subscribe_bg_events(session_id: str, user_id: str) -> asyncio.Queue:
    """在调用方事件循环上注册订阅（SSE 端点连接时调用）。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.setdefault(session_id, []).append((loop, queue, user_id))
        first = len(_SUBSCRIBERS[session_id]) == 1
    if first and _SIGNAL_BRIDGE is not None:
        _SIGNAL_BRIDGE.ensure_pump(
            "bg-tasks", session_id,
            lambda payload: _deliver_local(session_id, payload),
        )
    return queue

def unsubscribe_bg_events(session_id: str, queue: asyncio.Queue) -> None:
    with _SUBSCRIBERS_LOCK:
        subs = _SUBSCRIBERS.get(session_id) or []
        _SUBSCRIBERS[session_id] = [s for s in subs if s[1] is not queue]
        remaining = len(_SUBSCRIBERS[session_id])
        if not remaining:
            _SUBSCRIBERS.pop(session_id, None)
    if not remaining and _SIGNAL_BRIDGE is not None:
        _SIGNAL_BRIDGE.release_pump("bg-tasks", session_id)

def _deliver_local(session_id: str, payload: dict) -> None:
    """远端泵投回本地订阅者（不做 user 过滤——通道即会话，会话唯一属于一个 user）。"""
    with _SUBSCRIBERS_LOCK:
        subs = list(_SUBSCRIBERS.get(session_id) or [])
    for loop, queue, _user in subs:
        def _put(q: asyncio.Queue = queue, p: dict = payload) -> None:
            try:
                q.put_nowait(p)
            except asyncio.QueueFull:
                pass
        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass

def _bridge_publish(session_id: str, payload: dict) -> None:
    if _SIGNAL_BRIDGE is not None:
        _SIGNAL_BRIDGE.publish("bg-tasks", session_id, payload)

def publish_session_event(
    session_id: str, user_id: str, payload: dict[str, Any]
) -> None:
    """向该会话订阅者推送会话级事件（如 continuation run 启动）。"""
    _bridge_publish(session_id, payload)
    with _SUBSCRIBERS_LOCK:
        subs = list(_SUBSCRIBERS.get(session_id) or [])
    for loop, queue, sub_user in subs:
        if user_id not in (None, sub_user):
            continue

        def _put(q: asyncio.Queue = queue, p: dict[str, Any] = payload) -> None:
            try:
                q.put_nowait(p)
            except asyncio.QueueFull:
                pass

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass

def subscribe_run_events(run_id: str, user_id: str) -> asyncio.Queue:
    """按标准 AgentRun 订阅 child session 事件（详情打开时使用）。

    per-run 订阅上限与主链路同一份配置（超限抛 SubscriptionLimitExceeded，
    端点映射 429）。
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
        if core is None:
            core = DeliveryCore(
                max_buffer_events=StreamConfig.run_event_buffer_max_events,
                max_buffer_bytes=StreamConfig.run_event_buffer_max_bytes,
            )
            _RUN_DELIVERY[run_id] = core
        if len(core.subscribers) >= StreamConfig.run_max_subscriptions_per_run:
            raise SubscriptionLimitExceeded(
                f"per-run subscription limit exceeded: run={run_id} "
                f"max={StreamConfig.run_max_subscriptions_per_run}"
            )
        core.subscribers.append((loop, queue, user_id))
    return queue

def unsubscribe_run_events(run_id: str, queue: asyncio.Queue) -> None:
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
        if core is not None:
            core.subscribers = [s for s in core.subscribers if s[1] is not queue]

def get_run_event_history(run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
    """按投递内核重放：断档只发快照（首帧 run-snapshot），不假装连续补齐。

    首连（after_sequence<=0）只放行 run.started——内容恢复走首帧快照。
    """
    with _RUN_DELIVERY_LOCK:
        core = _RUN_DELIVERY.get(run_id)
    if core is None:
        return []
    if after_sequence <= 0:
        return [
            item.payload for item in core.buffer
            if isinstance(item.payload, dict) and item.payload.get("type") == "run.started"
        ]
    replay, snapshot_required = core.replay_after(after_sequence)
    if snapshot_required:
        return []
    return [item.payload for item in replay]

def _put_run_subscriber(sub: "_BGSub", payload: dict[str, Any]) -> None:
    """跨 loop 投递到订阅队列（满则丢弃——重连方由快照+重放恢复）。"""
    loop, queue, _user = sub

    def _put(q: asyncio.Queue = queue, p: dict[str, Any] = payload) -> None:
        try:
            q.put_nowait(p)
        except asyncio.QueueFull:
            pass

    try:
        loop.call_soon_threadsafe(_put)
    except RuntimeError:
        pass

def _publish_run_event(
    task: BackgroundTask,
    event: str,
    *,
    content: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    wire: Optional[dict[str, Any]] = None,
    transient: bool = False,
    sequence: Optional[int] = None,
) -> None:
    """发布子会话 run 事件（统一投递内核：重放缓存 + 在线订阅双通道）。

    wire：桥接层 wire 帧字段（text-delta 等），原样并入 payload。
    transient：瞬态事件（流式 delta / 实时统计）——只发在线订阅、不占
    sequence、不进缓存：重连方由 run-snapshot 全量内容恢复，回放叠加旧
    delta 反而重复。durable 事件的 sequence 由内核分配（投影事件可经
    ``sequence=`` 显式指定，与投影落库 guard 同号——投递与 DB 一个数空间）。
    """
    if not task.run_id:
        return
    core = _delivery_core(task.run_id)
    with _RUN_DELIVERY_LOCK:
        if transient:
            payload = {
                "type": event,
                "run_id": task.run_id,
                "session_id": task.child_session_id or task.task_id,
                "sequence": core.next_sequence - 1,
                "status": task.status.value,
                "transient": True,
            }
        else:
            if sequence is None:
                sequence = core.assign_sequence()
            payload = {
                "type": event,
                "run_id": task.run_id,
                "session_id": task.child_session_id or task.task_id,
                "sequence": sequence,
                "status": task.status.value,
            }
        if wire is not None:
            payload.update(wire)
        if finish_reason:
            payload["finish_reason"] = finish_reason
        # 终态时间：前端据此冻结 duration（重放历史事件同样可得）
        if task.completed_at is not None:
            payload["finished_at"] = task.completed_at
        if content is not None:
            payload["content"] = content
        if context is not None:
            payload["context"] = context
        subscribers = list(core.subscribers)
        if not transient:
            core.commit(SequencedPayload(sequence, payload))
    for sub in subscribers:
        if task.user_id not in (None, sub[2]):
            continue
        _put_run_subscriber(sub, payload)
    _publish_run_event_to_bus(payload)
    if event == "run.finished":
        def _expire_delivery(run_id: str = task.run_id) -> None:
            with _RUN_DELIVERY_LOCK:
                _RUN_DELIVERY.pop(run_id, None)

        timer = threading.Timer(300.0, _expire_delivery)
        timer.daemon = True
        timer.start()
    # 父会话只接收摘要目录更新；正文仍只在 child drawer 打开时订阅 run SSE。
    if task.child_session_id:
        from noesis.agents.background.ports import child_session_summary

        publish_session_event(
            task.session_id,
            task.user_id,
            {
                "event": "child-session",
                "child": child_session_summary(
                    task.to_dict(include_progress=False), parent_id=task.session_id,
                ),
            },
        )

def _publish_task_event(task: BackgroundTask, event: str) -> None:
    """向该会话所有订阅者推送任务快照事件；慢消费者丢事件（重连快照兜底）。"""
    payload = {"event": event, "task": task.to_dict(include_progress=False)}
    _bridge_publish(task.session_id, payload)
    with _SUBSCRIBERS_LOCK:
        subs = list(_SUBSCRIBERS.get(task.session_id) or [])
    for loop, queue, user_id in subs:
        if task.user_id not in (None, user_id):
            continue

        def _put(q: asyncio.Queue = queue, p: dict = payload) -> None:
            try:
                q.put_nowait(p)
            except asyncio.QueueFull:
                pass

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # 订阅者 loop 已关闭（SSE 断开竞态）
