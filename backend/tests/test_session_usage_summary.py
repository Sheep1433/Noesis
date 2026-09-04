"""会话 usage 汇总口径：主+子合并累计，turns 只数主会话轮次。

刷新后统计条（usage-summary / registry seed）必须与流式实时口径一致：
子会话消息的 usage 并入合计，但不计轮——实时侧 SessionStatsMiddleware
仅主 Agent 计轮，子会话消息是子 Agent 执行明细而非用户轮次。
"""

from __future__ import annotations

import pytest

from noesis.services.qa import helpers

MAIN = "sess-main"
CHILD = "sess-child"
USER = "u1"


class _FakeResult:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows: list):
        self._rows = rows

    async def execute(self, stmt):  # noqa: ARG002
        return _FakeResult(self._rows)


def _usage(steps: int, inp: int, cached: int) -> dict:
    return {"steps": steps, "input_tokens": inp, "cache_read_tokens": cached}


@pytest.mark.asyncio
async def test_usage_summary_merges_children_but_turns_count_main_only() -> None:
    db = _FakeDb([
        (MAIN, {"usage": _usage(steps=2, inp=10_000, cached=9_000)}),
        (MAIN, {"usage": _usage(steps=1, inp=5_000, cached=4_000)}),
        (CHILD, {"usage": _usage(steps=3, inp=2_000, cached=600)}),
    ])
    totals = await helpers.get_session_usage_summary(MAIN, USER, db)
    assert totals is not None
    assert totals["steps"] == 6
    assert totals["input_tokens"] == 17_000
    assert totals["cache_read_tokens"] == 13_600
    assert totals["turns"] == 2, "turns 只数主会话 assistant 消息，子会话不计轮"


@pytest.mark.asyncio
async def test_usage_summary_returns_none_without_usage() -> None:
    assert await helpers.get_session_usage_summary(MAIN, USER, _FakeDb([])) is None


@pytest.mark.asyncio
async def test_registry_seed_counts_main_turns_only(monkeypatch) -> None:
    seeded: dict = {}

    class _FakeRegistry:
        @staticmethod
        def peek(session_id):  # noqa: ARG004
            return None

        @staticmethod
        def seed(session_id, totals):
            seeded["session_id"] = session_id
            seeded["totals"] = totals

    monkeypatch.setattr(helpers, "SessionStatsRegistry", _FakeRegistry)
    db = _FakeDb([
        (MAIN, {"usage": _usage(steps=1, inp=3_000, cached=2_000)}),
        (CHILD, {"usage": _usage(steps=2, inp=1_000, cached=100)}),
    ])
    await helpers.seed_session_stats_from_history(MAIN, USER, db)
    assert seeded["session_id"] == MAIN
    assert seeded["totals"]["steps"] == 3
    assert seeded["totals"]["input_tokens"] == 4_000
    assert seeded["totals"]["turns"] == 1, "seed 的 turns 与实时口径一致：子会话不计轮"
