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
