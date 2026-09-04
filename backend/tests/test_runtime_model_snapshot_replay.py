"""运行时模型快照的跨线程重放契约。

背景（2026-09-03：子 Agent 配置 glm 实际跑 kilo）：子 Agent 在隔离线程
内编译 worker，``ContextVar`` 不跨线程，get_llm 看不到父 run 的快照，
自定义复合 id 落到目录解析被静默回退平台默认。修复=父侧捕获纯数据快照、
隔离线程开局 ``replay_runtime_model_snapshot`` 重放。
"""

from __future__ import annotations

import asyncio
import threading

from noesis.llm.runtime_snapshot import (
    RuntimeModelSnapshot,
    get_runtime_model_snapshot,
    replay_runtime_model_snapshot,
    set_runtime_model_snapshot,
    set_runtime_model_snapshots,
)


def _snapshot(model_id: str = "token/glm-5.3-flash") -> RuntimeModelSnapshot:
    return RuntimeModelSnapshot(
        id=model_id,
        provider_id="p-1",
        purpose="chat",
        model_type="openai",
        base_url="https://example.invalid/v1",
        api_key="sk-test",
        wire_name="glm-5.3-flash",
    )


def test_replay_in_fresh_thread_restores_visibility() -> None:
    """新线程默认看不到父线程快照；重放后按 id 可查。"""
    seen: dict = {}

    def worker() -> None:
        seen["before"] = get_runtime_model_snapshot("token/glm-5.3-flash")
        replay_runtime_model_snapshot(_snapshot(), target_model_id="token/glm-5.3-flash")
        seen["after"] = get_runtime_model_snapshot("token/glm-5.3-flash")

    set_runtime_model_snapshot(_snapshot())
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen["before"] is None, "隔离线程不应继承父线程快照"
    assert seen["after"] is not None
    assert seen["after"].id == "token/glm-5.3-flash"
    assert seen["after"].wire_name == "glm-5.3-flash"


def test_replay_skipped_when_target_model_differs() -> None:
    """覆盖到别的模型（followup 按 turn 切换）时不重放旧快照——宁报错不用错。"""
    seen: dict = {}

    def worker() -> None:
        replay_runtime_model_snapshot(
            _snapshot("token/glm-5.3-flash"), target_model_id="opencode/big-pickle"
        )
        seen["snapshot"] = get_runtime_model_snapshot("opencode/big-pickle")
        seen["any"] = get_runtime_model_snapshot()

    set_runtime_model_snapshots([])
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen["snapshot"] is None
    assert seen["any"] is None


def test_replay_none_is_noop() -> None:
    """父 run 无自定义模型（平台默认）：重放为空操作，目录默认照常生效。"""
    set_runtime_model_snapshots([])

    replay_runtime_model_snapshot(None, target_model_id="kilo-auto/free")

    assert get_runtime_model_snapshot() is None


def test_replay_works_across_asyncio_loops() -> None:
    """隔离 loop（子 Agent 实际形态）：隔离 loop 的 task 内重放同样可见。

    executor 的隔离线程跑自己的 event loop；重放动作发生在隔离 loop 内，
    快照必须对随后该 loop 上的 get_runtime_model_snapshot 可见。
    """
    seen: dict = {}

    async def isolated_loop_main() -> None:
        seen["before"] = get_runtime_model_snapshot("token/glm-5.3-flash")
        replay_runtime_model_snapshot(
            _snapshot(), target_model_id="token/glm-5.3-flash"
        )
        seen["after"] = get_runtime_model_snapshot("token/glm-5.3-flash")

    set_runtime_model_snapshots([])
    isolated_loop = asyncio.new_event_loop()
    try:
        isolated_loop.run_until_complete(isolated_loop_main())
    finally:
        isolated_loop.close()

    assert seen["before"] is None
    assert seen["after"] is not None
