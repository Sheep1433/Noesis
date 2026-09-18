"""`noesis chat -p` 的 DB 跑次：复用生产 headless 入口，CLI 不再有第二套事件处理。

- run_channel_agent（channel_run_service）：会话行、用户消息 SSOT、TAgentRun、
  检查点与终态落库全走生产路径，观测性直接查 DB / Langfuse；
- run_manager.subscribe + encode_sequenced_event：与 web SSE 端点同源的
  事件流回显（raw 底账 = 可重放的 SSE 流）；
- 终值 record 以 ChannelRunResult 与 RunCompleted 载荷为准（DB 权威），
  不从流式文本重新拼接。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable

from noesis.chat.delivery.events import (
    RunAborted,
    RunCompleted,
    RunError,
    RunSnapshotReplaced,
    StreamDone,
)
from noesis.chat.delivery.sse import encode_sequenced_event, format_sse
from noesis.chat.runs import SlowSubscriber
from noesis.config.checkpointer import close_checkpointer, init_checkpointer
from noesis.runtime.logging import logger

#: 订阅等待 run_manager 注册的上限（run_channel_agent 启动即注册，秒级足够）
_SUBSCRIBE_WAIT_SECONDS = 30.0
#: run 结束后队列排空的等待窗口
_DRAIN_TIMEOUT_SECONDS = 2.0


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _validate_db_run_args(user_id: str, session_id: str) -> None:
    if not _is_uuid(user_id):
        raise ValueError(
            f"DB 模式需要 NOESIS_USER_ID 为真实账号 UUID（当前 {user_id!r}）"
        )
    if len(session_id) > 36:
        raise ValueError("session id 超长（t_chat_session.id VARCHAR(36)）")


def _prepare_db_run() -> None:
    # 与 server lifespan 同款注册：子 Agent executor 在隔离线程 loop 上跑，
    # 其 DB 落库（mark_started / 投影 / 终态）经 run_on_main_loop 调度回主
    # loop；不注册则全部静默跳过（冒烟实测：子 run 永远 queued、子会话
    # assistant 消息空骨架）
    from noesis.runtime.main_loop import capture_main_loop

    capture_main_loop()


def _make_pump(
    run_manager: Any,
    run_id: str,
    *,
    emit_stream: bool,
    out: Callable[[str], None],
    terminal: dict[str, Any],
    run_done: asyncio.Event,
) -> Any:
    """订阅 run 事件流：回显（stream-json）+ 终值采集（usage/error）。"""

    async def pump() -> None:
        subscription = await _subscribe_with_wait(run_manager, run_id)
        if subscription is None:
            logger.warning("CLI 订阅失败，事件回显与终值采集降级 run_id=%s", run_id)
            return
        try:
            # 首连契约（同 chat_api）：先发快照补齐订阅前已发布的事件，再接活流
            if emit_stream:
                out(format_sse(
                    "run-snapshot",
                    {"type": "run-snapshot", **subscription.snapshot.to_dict()},
                ))
            while True:
                try:
                    item = await asyncio.wait_for(
                        subscription.queue.get(),
                        timeout=_DRAIN_TIMEOUT_SECONDS if run_done.is_set() else 15.0,
                    )
                except asyncio.TimeoutError:
                    if run_done.is_set():
                        return
                    continue
                if isinstance(item, SlowSubscriber):
                    return
                event = item.event
                if isinstance(event, RunCompleted):
                    terminal["usage"] = dict(event.usage or {})
                    terminal["model_calls"] = list(event.model_calls or [])
                elif isinstance(event, RunError):
                    terminal["error"] = event.message
                elif isinstance(event, RunAborted):
                    terminal.setdefault("error", event.message or "run aborted")
                if emit_stream:
                    for line in encode_sequenced_event(item):
                        out(line)
                run_manager.record_event_delivered(item)
                if isinstance(
                    event,
                    (RunCompleted, RunAborted, RunError, RunSnapshotReplaced, StreamDone),
                ):
                    return
        finally:
            await subscription.close()

    async def guarded() -> None:
        try:
            await pump()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # 回显/终值采集失败不炸跑次：DB 仍是权威，事后可补收
            logger.exception("CLI SSE pump 异常 run_id=%s", run_id)

    return guarded()


async def run_db_print(
    *,
    query: str,
    session_id: str,
    user_id: str,
    model_id: str | None,
    out: Callable[[str], None],
    emit_stream: bool = True,
) -> dict[str, Any]:
    """单问 DB 跑次（super）：返回评测记录（同 stream-json `__tw_result__` 形状）。

    emit_stream=False 时事件流只用于终值采集，不写 stdout（text/json 输出）。
    """
    from noesis.services.channel_run_service import run_channel_agent
    from noesis.services.run_service import run_manager

    _validate_db_run_args(user_id, session_id)
    _prepare_db_run()
    await init_checkpointer()
    run_id = str(uuid.uuid4())
    if emit_stream:
        out(json.dumps({
            "type": "__tw_init__",
            "session_id": session_id,
            "user_id": user_id,
            "model": model_id,
            "run_id": run_id,
        }, ensure_ascii=False))

    t0 = time.perf_counter()
    terminal: dict[str, Any] = {}
    run_done = asyncio.Event()

    async def launch() -> Any:
        try:
            return await run_channel_agent(
                user_id=user_id,
                session_id=session_id,
                query=query,
                origin="cli",
                model_id=model_id,
                run_id=run_id,
            )
        finally:
            run_done.set()

    pump = asyncio.create_task(
        _make_pump(run_manager, run_id, emit_stream=emit_stream, out=out,
                   terminal=terminal, run_done=run_done),
        name=f"cli-sse-pump:{run_id}",
    )
    try:
        result = await launch()
    except BaseException:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        raise
    # run 收口后终态事件仍在队列里：给 pump 一个排空窗口再放弃
    try:
        await asyncio.wait_for(pump, timeout=_DRAIN_TIMEOUT_SECONDS * 3)
    except asyncio.TimeoutError:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    return _build_record(
        result=result,
        run_id=run_id,
        terminal=terminal,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


async def _subscribe_with_wait(run_manager: Any, run_id: str) -> Any:
    """等 run_manager 注册后订阅（run_channel_agent 启动毫秒级；注册前 get 抛 KeyError）。

    首连无重放（replay_after(0) 为空）：订阅前已发布的事件由 run-snapshot
    快照补齐，与 web SSE 首连同语义。
    """
    deadline = time.monotonic() + _SUBSCRIBE_WAIT_SECONDS
    while True:
        try:
            return await run_manager.subscribe(run_id, after_sequence=0)
        except KeyError:
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.05)


def _build_record(*, result: Any, run_id: str, terminal: dict, latency_ms: int) -> dict[str, Any]:
    from noesis_cli.streamjson import flag_empty_completion

    finish_reason = result.finish_reason or "stop"
    error = terminal.get("error")
    if result.hitl_pending:
        error = "hitl_pending：等待审批（headless 评测不应出现）"
    record = {
        "completed": error is None and not result.hitl_pending
        and finish_reason in ("stop", "completed"),
        "finish_reason": finish_reason,
        "final_text": result.plain_text or "",
        "session_id": result.session_id,
        "run_id": result.run_id or run_id,
        "assistant_message_id": result.assistant_message_id,
        "usage": terminal.get("usage") or {},
        "model_calls": terminal.get("model_calls") or [],
        "input_tokens": int((terminal.get("usage") or {}).get("input_tokens") or 0),
        "output_tokens": int((terminal.get("usage") or {}).get("output_tokens") or 0),
        "error": error,
        "latency_ms": latency_ms,
    }
    return flag_empty_completion(record)


async def shutdown_db_run() -> None:
    """进程收尾：释放共享 Postgres checkpointer 连接池，flush Langfuse 缓冲。

    CLI 是短命进程：Langfuse SDK 的批量缓冲靠显式 flush，server 长驻进程
    有周期 flush 兜底，CLI 不 flush 则 trace 随进程退出丢失（冒烟实测）。
    """
    try:
        await close_checkpointer()
    except Exception:  # noqa: BLE001
        logger.exception("close_checkpointer 失败（进程退出路径，忽略）")
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse flush 失败（进程退出路径，忽略）", exc_info=True)
