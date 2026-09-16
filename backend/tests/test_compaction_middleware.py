from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from types import SimpleNamespace

from noesis.agents.middlewares.compaction_middleware import (
    CompactionMiddleware,
    CompactionThresholds,
)


# 有效摘要的测试替身：须超过生产最小体量校验（_MIN_SUMMARY_CHARS）
_VALID_SUMMARY = "checkpoint: " + "section content. " * 120
def _thresholds(auto_at: int = 50) -> CompactionThresholds:
    return CompactionThresholds(
        model_input_limit=auto_at + 210,
        summary_output_reserve=10,
        transient_request_buffer=200,
    )


def _request(messages, state=None, tools=()) -> ModelRequest:
    state = state if state is not None else {"messages": list(messages)}
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(messages),
        system_message=SystemMessage(content="system"),
        tools=list(tools),
        state=state,
    )


def _response() -> ModelResponse:
    return ModelResponse(result=[AIMessage(content="ok")])


def _messages(count: int = 20):
    return [HumanMessage(content=f"message {index} " + "x" * 20) for index in range(count)]


def _command_update(result) -> dict:
    assert isinstance(result, ExtendedModelResponse)
    assert result.command is not None
    return result.command.update


def test_threshold_accounts_for_system_and_tool_schemas() -> None:
    seen = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: len(messages) * 10,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(35),
        keep_messages=2,
        user_message_budget_tokens=0,  # 本用例只测阈值核算，关闭原话装回
    )
    middleware.wrap_model_call(
        _request(_messages(8), tools=["tool schema " * 30]),
        lambda request: seen.append(request) or _response(),
    )
    assert seen[0].messages[0].content.startswith("[上下文状态]")


def test_compaction_projects_history_and_persists_event_without_mutating_raw() -> None:
    raw = _messages()
    seen = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=4,
    )
    result = middleware.wrap_model_call(
        _request(raw), lambda request: seen.append(request) or _response()
    )
    update = _command_update(result)
    assert len(raw) == 20
    # 最终请求 = [被压缩区用户原话(cutoff=16 前 16 条), summary, 保留尾 4 条]
    assert len(seen[0].messages) == 16 + 1 + 4
    assert [m.content for m in seen[0].messages[:16]] == [m.content for m in raw[:16]]
    assert seen[0].messages[16].additional_kwargs["lc_source"] == "summarization"
    assert [m.content for m in seen[0].messages[17:]] == [m.content for m in raw[16:]]
    assert update["compaction"]["event"]["cutoff_index"] == 16
    assert "messages" not in update

    resumed_state = {"messages": [*raw, HumanMessage(content="new turn")], **update}
    projected = middleware._project(_request(resumed_state["messages"], resumed_state))
    assert len(projected.messages) == 1 + 4 + 1
    assert projected.messages[0].additional_kwargs["lc_source"] == "summarization"


@pytest.mark.parametrize("summary", [
    "",
    "<error> failed",
    "I cannot summarize this",
    # 过短退化解：766K 输入实测产出 423 字符的原文片段复述（回显指令
    # 模板头 + 尾部片段），骗过前缀/复读检测后静默替换真实历史
    "[上下文状态] 本会话历史已压缩。\n\n[调用工具 Bash] {\"command\": \"ls\"}",
])
def test_invalid_summary_does_not_publish_event(summary: str) -> None:
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: summary,
        thresholds=_thresholds(),
    )
    result = middleware.wrap_model_call(_request(_messages()), lambda request: _response())
    update = _command_update(result)
    assert update["compaction"]["consecutive_failures"] == 1
    assert "event" not in update["compaction"]


def test_summary_ptl_retry_drops_complete_tool_round() -> None:
    calls = []
    transcript = [
        HumanMessage(content="first"),
        AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "call-1"}]),
        ToolMessage(content="large", tool_call_id="call-1"),
        *_messages(12),
    ]

    def summarize(messages):
        calls.append(list(messages))
        if len(calls) == 1:
            raise ContextOverflowError("summary too long")
        return _VALID_SUMMARY

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=summarize,
        thresholds=_thresholds(),
        keep_messages=2,
    )
    middleware.wrap_model_call(_request(transcript), lambda request: _response())
    assert len(calls) == 2
    remaining_ids = {
        message.tool_call_id for message in calls[1] if isinstance(message, ToolMessage)
    }
    ai_ids = {
        call["id"]
        for message in calls[1]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    assert remaining_ids <= ai_ids


def test_reactive_overflow_retries_once_and_persists_recovery() -> None:
    attempts = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 1,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=3,
    )

    def handler(request):
        attempts.append(len(request.messages))
        if len(attempts) == 1:
            raise ContextOverflowError("provider overflow")
        return _response()

    update = _command_update(middleware.wrap_model_call(_request(_messages()), handler))
    # 第一次无压缩事件：原样 20 条；反应式压缩后：17 条用户原话 + summary + 保留尾 3 条
    assert attempts == [20, 17 + 1 + 3]
    assert update["compaction"]["last_mode"] == "reactive"


def test_breaker_is_checkpointed_and_blocks_further_compaction() -> None:
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: "",
        thresholds=_thresholds(),
        max_consecutive_failures=2,
    )
    state = {"messages": _messages()}
    for expected in (1, 2):
        update = _command_update(
            middleware.wrap_model_call(
                _request(state["messages"], state), lambda request: _response()
            )
        )
        state.update(update)
        assert state["compaction"]["consecutive_failures"] == expected

    result = middleware.wrap_model_call(
        _request(state["messages"], state), lambda request: _response()
    )
    assert not isinstance(result, ExtendedModelResponse)


@pytest.mark.asyncio
async def test_async_summary_path_uses_async_callable() -> None:
    async def summarize(messages):
        return _VALID_SUMMARY

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: "sync must not run",
        async_summarize=summarize,
        thresholds=_thresholds(),
        keep_messages=4,
    )

    async def handler(request):
        return _response()

    result = await middleware.awrap_model_call(_request(_messages()), handler)
    assert _command_update(result)["compaction"]["event"]


def test_post_compact_preserves_system_message_with_stable_sources() -> None:
    """compaction 只替换 messages，不动 system_message——稳定来源（Dynamic/Durable 注入）自动保留。"""
    stable_system = SystemMessage(content="system prompt\n## Dynamic Context\n当前日期: 2026-08-15\n## Durable Context\nactive_plan: do task X")
    seen = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
        user_message_budget_tokens=0,  # 本用例只测 system_message 保留
    )
    raw = _messages(10)
    request = ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=list(raw),
        system_message=stable_system,
        tools=[],
        state={"messages": list(raw)},
    )
    middleware.wrap_model_call(request, lambda req: seen.append(req) or _response())
    # compaction 替换了 messages（summary + preserved tail），但 system_message 保留
    assert seen[0].system_message is stable_system
    assert "Dynamic Context" in seen[0].system_message.content
    assert "active_plan" in seen[0].system_message.content
    assert len(seen[0].messages) < len(raw)  # messages 确实被压缩了


def test_post_compact_preserves_private_state() -> None:
    """compaction 只替换 messages 和 compaction policy，不动其他 private state（Skills/Memory）。"""
    seen = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
    )
    raw = _messages(10)
    state = {
        "messages": list(raw),
        "skills_metadata": [{"name": "deep-research-v2"}],
        "memory_contents": "user prefers concise answers",
    }
    middleware.wrap_model_call(
        _request(raw, state),
        lambda req: seen.append(req) or _response(),
    )
    # Skills/Memory state 保留
    assert seen[0].state.get("skills_metadata") == [{"name": "deep-research-v2"}]
    assert seen[0].state.get("memory_contents") == "user prefers concise answers"


def test_manual_compact_bypasses_threshold_and_breaker() -> None:
    """manual compact 请求绕过阈值和 breaker 检查。"""
    seen = []
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 1,  # 远低于 auto_compact_at
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
        max_consecutive_failures=2,
        user_message_budget_tokens=0,  # 本用例只测 manual 绕过逻辑
    )
    # 先让 breaker 进入熔断状态
    state = {"messages": _messages(10), "compaction": {"consecutive_failures": 99}}
    # 手动 compact 请求：在 state 里设标记不会绕过——需要在 runtime.context 里设
    # 但测试里没有 runtime，直接测 _should_auto_compact 的逻辑
    from unittest.mock import MagicMock
    request = _request(_messages(10), state)
    # 模拟 runtime.context 有 manual_compact_requested
    request_runtime = MagicMock()
    request_runtime.context = {"manual_compact_requested": True}
    request = request.override(runtime=request_runtime)  # type: ignore[arg-type]
    # breaker 熔断 + token 远低于阈值，但 manual compact 应该仍然触发
    assert middleware._should_auto_compact(request) is True
    # 实际执行压缩
    result = middleware.wrap_model_call(request, lambda req: seen.append(req) or _response())
    update = _command_update(result)
    assert update["compaction"]["last_mode"] == "manual"
    assert len(seen[0].messages) < 10  # messages 被压缩了


@pytest.mark.asyncio
async def test_host_manual_compaction_updates_policy_without_model_turn() -> None:
    calls = []
    checkpoints = []

    async def summarize(messages):
        calls.append(list(messages))
        return _VALID_SUMMARY

    middleware = CompactionMiddleware(
        token_counter=lambda messages: len(messages) * 10,
        summarize=lambda messages: _VALID_SUMMARY,
        async_summarize=summarize,
        thresholds=_thresholds(),
        keep_messages=2,
    )
    state = {"messages": _messages(10)}

    async def checkpoint(update):
        checkpoints.append(update)

    compacted = await middleware.acompact_state(
        state,
        "session-host",
        checkpoint=checkpoint,
    )

    assert compacted is not None
    assert len(calls) == 1
    assert compacted.result.mode == "manual"
    # pre 无压缩事件 → 原样 10；post = 8 条用户原话 + summary + 保留尾 2
    assert compacted.pre_message_count == 10
    assert compacted.post_message_count == 8 + 1 + 2
    assert checkpoints[0]["compaction"]["event"]["cutoff_index"] == 8
    assert "compaction" not in state


# ---------- 压缩边界写入（session-history-search） ----------


@pytest.mark.asyncio
async def test_async_auto_compaction_invokes_boundary_writer() -> None:
    written = []

    async def writer(thread_id: str) -> None:
        written.append(thread_id)

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
        boundary_writer=writer,
    )
    request = _request(_messages(10))
    request.runtime = SimpleNamespace(context={"thread_id": "session-boundary"})

    async def handler(req):  # noqa: ARG001
        return _response()

    await middleware.awrap_model_call(request, handler)
    assert written == ["session-boundary"]


@pytest.mark.asyncio
async def test_boundary_writer_failure_does_not_break_model_call() -> None:
    async def writer(thread_id: str) -> None:
        raise RuntimeError("boundary write failed")

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
        boundary_writer=writer,
    )

    async def handler(req):  # noqa: ARG001
        return _response()

    result = await middleware.awrap_model_call(_request(_messages(10)), handler)
    assert result.model_response.result[0].content == "ok"


@pytest.mark.asyncio
async def test_host_manual_compaction_invokes_boundary_writer() -> None:
    written = []

    async def writer(thread_id: str) -> None:
        written.append(thread_id)

    async def summarize(messages):  # noqa: ARG001
        return _VALID_SUMMARY

    middleware = CompactionMiddleware(
        token_counter=lambda messages: len(messages) * 10,
        summarize=lambda messages: "unused",
        async_summarize=summarize,
        thresholds=_thresholds(),
        keep_messages=2,
        boundary_writer=writer,
    )

    async def checkpoint(update):  # noqa: ARG001
        pass

    compacted = await middleware.acompact_state(
        {"messages": _messages(10)}, "session-manual", checkpoint=checkpoint
    )
    assert compacted is not None
    assert written == ["session-manual"]


@pytest.mark.asyncio
async def test_sync_path_skips_async_boundary_writer() -> None:
    """同步 wrap_model_call 为离线评测路径：不接 DB，异步 writer 不被调用。"""
    written = []

    async def writer(thread_id: str) -> None:
        written.append(thread_id)

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
        boundary_writer=writer,
    )
    middleware.wrap_model_call(_request(_messages(10)), lambda req: _response())
    assert written == []


@pytest.mark.asyncio
async def test_no_compaction_no_boundary_write() -> None:
    """未触发压缩（低于阈值）时不得写边界。"""
    written = []

    async def writer(thread_id: str) -> None:
        written.append(thread_id)

    middleware = CompactionMiddleware(
        token_counter=lambda messages: 1,
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
        boundary_writer=writer,
    )

    async def handler(req):  # noqa: ARG001
        return _response()

    await middleware.awrap_model_call(_request(_messages(3)), handler)
    assert written == []


# ---------- 被压缩区用户原话装回（对齐 codex compact） ----------


def test_summary_message_carries_context_contract() -> None:
    """摘要消息头部携带上下文契约：构成/盲区/使用原则，按类别声明且与
    工具挂载无关（通用机制，不携带会话特定信息）。"""
    from noesis.agents.middlewares.compaction_middleware import (
        _CONTEXT_CONTRACT,
        CompactionMiddleware,
        CompactionResult,
    )

    _summary_message = CompactionMiddleware._summary_message

    result = CompactionResult(
        summary_text="checkpoint content. " * 200,
        preserved_messages=(HumanMessage(content="tail"),),
        original_message_count=10, mode="manual", attempts=1,
    )
    message = _summary_message(result)
    assert message.content.startswith(_CONTEXT_CONTRACT)
    # 契约的三个承诺逐项在场
    assert "当前上下文的构成" in message.content
    assert "可能不在你的视野内" in message.content          # 盲区声明
    assert "定向找回" in message.content                    # 恢复通道
    assert "不要翻找文件系统" in message.content            # 闭卷行为约束
    # 契约是静态脚手架：不含会话特定内容
    assert "{" not in _CONTEXT_CONTRACT


def test_retained_user_messages_reverse_fill_and_truncation() -> None:
    """从最新往最旧装满预算，最旧的装不下就截断；只收 HumanMessage 文字。"""
    from noesis.agents.middlewares.compaction_middleware import _retained_user_messages

    raw = [HumanMessage(content=f"用户消息 {i} " + "细" * 40) for i in range(6)]
    raw.insert(2, AIMessage(content="assistant 消息不装回"))
    raw.insert(3, ToolMessage(content="tool result", tool_call_id="t1"))
    # 每条约 47 字符 ≈ 11 token；预算 35 → 装下最新 3 条完整 + 最旧侧 1 条截断
    retained = _retained_user_messages(raw, 35)
    assert len(retained) == 4
    # 时间序：截断的最早一条在前，最新三条原样在后
    assert retained[0].content.endswith("…[truncated]")
    assert [m.content for m in retained[1:]] == [
        raw[5].content, raw[6].content, raw[7].content,
    ]


def test_retained_user_messages_strips_images_and_skips_empty() -> None:
    from noesis.agents.middlewares.compaction_middleware import _retained_user_messages

    with_image = HumanMessage(content=[
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
    ])
    image_only = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:x"}}])
    retained = _retained_user_messages([image_only, with_image], 1_000)
    assert len(retained) == 1
    assert retained[0].content == "看这张图"


def test_retained_user_messages_budget_zero_disables() -> None:
    from noesis.agents.middlewares.compaction_middleware import _retained_user_messages

    assert _retained_user_messages(_messages(5), 0) == []


def test_final_request_injection_respects_budget_and_event() -> None:
    """预算 0 或无压缩事件时最终请求与投影一致；有事件时头部装回原话。"""
    raw = _messages(10)
    middleware = CompactionMiddleware(
        token_counter=lambda messages: 200,  # 超过 auto_at，触发自动压缩
        summarize=lambda messages: _VALID_SUMMARY,
        thresholds=_thresholds(),
        keep_messages=2,
    )
    # 无事件 → 最终请求 = 原样
    request = _request(raw)
    assert middleware._final_request(middleware._project(request), request).messages == raw

    # 用真实压缩产出事件
    state = {"messages": list(raw)}
    result = middleware.wrap_model_call(
        _request(raw, state), lambda req: _response()
    )
    update = _command_update(result)
    resumed = {"messages": list(raw), **update}
    resumed_request = _request(resumed["messages"], resumed)
    final = middleware._final_request(middleware._project(resumed_request), resumed_request)
    # cutoff=8 → 8 条原话 + summary + 2 保留
    assert len(final.messages) == 8 + 1 + 2
    assert [m.content for m in final.messages[:8]] == [m.content for m in raw[:8]]
