"""stream_agent_events 的事件过滤契约：压缩摘要调用的模型事件不得外泄。

摘要生成（factory._compaction_deps 的 summarize/async_summarize）在图内执行，
其 astream_events 事件若进入消息投影，摘要 JSON 会被 bridge 当成正文
text part 上屏（assistant 消息里出现 user_goals/decisions 等原始 JSON），
usage/步数也会混入该基础设施调用——故按 run tag 在源头丢弃。
"""

from __future__ import annotations

import pytest

from noesis.agents.middlewares.compaction_middleware import COMPACTION_SUMMARY_TAG
from noesis.runtime.stream import stream_agent_events


class _FakeAgent:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def astream_events(self, stream_input, config=None):  # noqa: ANN001, ANN202
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_compaction_summary_events_are_dropped() -> None:
    events = [
        {"event": "on_chat_model_start", "run_id": "r-main", "tags": []},
        {
            "event": "on_chat_model_end",
            "run_id": "r-summary",
            "tags": [COMPACTION_SUMMARY_TAG],
            "data": {"output": {"content": '{"user_goals": []}'}},
        },
        {"event": "on_chat_model_end", "run_id": "r-main", "tags": []},
    ]
    agent = _FakeAgent(events)

    out: list[dict] = []
    async for item in stream_agent_events(
        agent, {"input": {"messages": []}}, task_id="t-filter", message_id="m-filter",
    ):
        out.append(item)

    run_ids = [str(item.get("run_id")) for item in out if item.get("event")]
    assert "r-summary" not in run_ids, "压缩摘要调用的事件必须被过滤"
    assert "r-main" in run_ids
    # 正常收尾哨兵不受影响
    assert any(item.get("type") == "__tw_finish__" for item in out)


def _fallback_message(content: str = "服务暂时不可用，请稍候重试。"):
    from langchain_core.messages import AIMessage
    from noesis.agents.middlewares.llm_error_handling_middleware import FALLBACK_MARKER

    return AIMessage(content=content, additional_kwargs={FALLBACK_MARKER: True})


@pytest.mark.asyncio
async def test_fallback_final_output_finishes_with_error() -> None:
    """最后一次模型输出是降级失败说明 → error 收尾，不得伪装成正常空完成。

    CLI / 离线评测直接消费事件流，SSE 桥接层的 custom 事件兜底够不到它们。
    """
    events = [
        {
            "event": "on_chat_model_end",
            "run_id": "r-main",
            "tags": [],
            "data": {"output": _fallback_message("API 额度不足，请检查 provider 账户后重试。")},
        },
    ]
    out: list[dict] = []
    async for item in stream_agent_events(
        _FakeAgent(events), {"input": {"messages": []}},
        task_id="t-fb", message_id="m-fb",
    ):
        out.append(item)

    errors = [i for i in out if i.get("type") == "__tw_error__"]
    assert errors and "额度不足" in str(errors[0].get("content"))
    finishes = [i for i in out if i.get("type") == "__tw_finish__"]
    assert finishes and finishes[-1].get("finish_reason") == "error"


@pytest.mark.asyncio
async def test_fallback_recovered_by_later_success_finishes_normally() -> None:
    """降级后又有成功模型输出（重试成功跨轮）→ 正常 stop 收尾。"""
    from langchain_core.messages import AIMessage

    events = [
        {
            "event": "on_chat_model_end",
            "run_id": "r-1",
            "tags": [],
            "data": {"output": _fallback_message()},
        },
        {
            "event": "on_chat_model_end",
            "run_id": "r-2",
            "tags": [],
            "data": {"output": AIMessage(content="正常回答")},
        },
    ]
    out: list[dict] = []
    async for item in stream_agent_events(
        _FakeAgent(events), {"input": {"messages": []}},
        task_id="t-fb2", message_id="m-fb2",
    ):
        out.append(item)

    assert not [i for i in out if i.get("type") == "__tw_error__"]
    finishes = [i for i in out if i.get("type") == "__tw_finish__"]
    assert finishes and finishes[-1].get("finish_reason") == "stop"


@pytest.mark.asyncio
async def test_normal_run_still_finishes_with_stop() -> None:
    from langchain_core.messages import AIMessage

    events = [
        {
            "event": "on_chat_model_end",
            "run_id": "r-main",
            "tags": [],
            "data": {"output": AIMessage(content="正常回答")},
        },
    ]
    out: list[dict] = []
    async for item in stream_agent_events(
        _FakeAgent(events), {"input": {"messages": []}},
        task_id="t-ok", message_id="m-ok",
    ):
        out.append(item)

    assert not [i for i in out if i.get("type") == "__tw_error__"]
    assert any(
        i.get("type") == "__tw_finish__" and i.get("finish_reason") == "stop"
        for i in out
    )


@pytest.mark.asyncio
async def test_fallback_custom_event_finishes_with_error() -> None:
    """降级信号走 custom 事件（真实路径：模型调用被中间件短路，无 model_end）。"""
    events = [
        {
            "event": "on_custom_event",
            "name": "noesis_model_fallback",
            "run_id": "r-main",
            "tags": [],
            "data": {"type": "noesis_model_fallback", "content": "API 额度不足，请检查 provider 账户后重试。"},
        },
    ]
    out: list[dict] = []
    async for item in stream_agent_events(
        _FakeAgent(events), {"input": {"messages": []}},
        task_id="t-fb3", message_id="m-fb3",
    ):
        out.append(item)

    errors = [i for i in out if i.get("type") == "__tw_error__"]
    assert errors and "额度不足" in str(errors[0].get("content"))
    finishes = [i for i in out if i.get("type") == "__tw_finish__"]
    assert finishes and finishes[-1].get("finish_reason") == "error"
