"""research-source-provenance 回归：检索同构投影、来源身份归一、跨边界登记。

覆盖 openspec change 的 1.3（子会话 retrieval parts 同构 / 工具输出摘要化 /
无检索零变化）、2.1（canonical URL 归一化，与前端 canonicalUrl.ts 共享用例集）、
3.4（通知 / check_task 两条通道的来源登记、多子 Agent 同源合并）。
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import PrivateAttr

from noesis.agents.subagents import notifications
from noesis.agents.subagents.executor import (
    BackgroundSubagentExecutor,
    BgTaskStatus,
    shutdown as bg_shutdown,
)
from noesis.agents.subagents.notify_middleware import BgNotifyMiddleware
from noesis.agents.subagents.tools import build_background_task_tools
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge
from noesis.chat.event_mapping.mapper import RuntimeEventMapper, new_stream_ctx
from noesis.chat.event_mapping.retrieval import (
    canonical_url,
    drain_pending_sources,
    register_pending_sources,
    source_identity,
)
from noesis.chat.message_builder import AssistantMessageBuilder

# ---------------------------------------------------------------------------
# 共享用例集：与 frontend/__tests__/canonicalUrl.test.ts 完全一致（2.1 前后端对齐）
# ---------------------------------------------------------------------------

CANONICAL_URL_CASES = [
    ("https://Example.com/A/?utm_source=x&b=2&a=1", "https://example.com/A?a=1&b=2"),
    ("http://example.com:80/a", "https://example.com/a"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("https://example.com/a#section", "https://example.com/a"),
    ("https://example.com/a/", "https://example.com/a"),
    ("https://example.com/a?fbclid=abc", "https://example.com/a"),
    ("https://example.com/a?utm_term=hello&q=llm", "https://example.com/a?q=llm"),
    ("https://example.com", "https://example.com/"),
    ("example.com/a", "https://example.com/a"),
    ("https://EXAMPLE.com:8443/Deep/path/", "https://example.com:8443/Deep/path"),
    ("", ""),
]


@pytest.mark.parametrize("raw,expected", CANONICAL_URL_CASES)
def test_canonical_url_shared_cases(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


def test_source_identity_uses_canonical_url() -> None:
    a = {"source_type": "web", "url": "https://example.com/a?utm_source=x", "title": "A"}
    b = {"source_type": "web", "url": "https://example.com/a", "title": "A"}
    assert source_identity(a) == source_identity(b)


# ---------------------------------------------------------------------------
# 1.x 检索解析同构：桥接层（主 / 子管道共用同一构造点）
# ---------------------------------------------------------------------------


def _web_search_result(url: str, title: str) -> dict[str, Any]:
    return {
        "source_type": "web",
        "url": url,
        "title": title,
        "excerpt": f"{title} 的摘要内容",
        "score": 0.87,
    }


def _tool_end_event(name: str, call_id: str, output: Any) -> dict[str, Any]:
    return {
        "event": "on_tool_end",
        "name": name,
        "run_id": call_id,
        "data": {"output": output},
    }


def _run_pipeline_events(session_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """以 executor 统一管道同款构造（bridge + mapper + new_stream_ctx）驱动事件。"""
    bridge = LangGraphSseBridge(session_id)
    builder = AssistantMessageBuilder(session_id=session_id, message_id=bridge.assistant_message_id)
    ctx = new_stream_ctx()
    mapper = RuntimeEventMapper(bridge)
    for event in events:
        mapper.map_item(event, builder, ctx)
    mapper.finalize()
    return builder.to_dict()


def test_web_search_tool_output_becomes_retrieval_part() -> None:
    """web_search JSON 输出 → retrieval part + 工具输出摘要化（主/子同构）。"""
    payload = json.dumps({
        "results": [_web_search_result("https://example.com/a", "来源 A")],
        "truncated": False,
    }, ensure_ascii=False)
    content = _run_pipeline_events("sess-rsp", [
        {
            "event": "on_tool_start",
            "name": "web_search",
            "run_id": "call-ws",
            "data": {"input": {"query": "llm 检索"}},
        },
        _tool_end_event("web_search", "call-ws", payload),
    ])
    retrieval_parts = [p for p in content["parts"] if p["type"] == "retrieval"]
    assert len(retrieval_parts) == 1
    part = retrieval_parts[0]
    assert part["tool_call_id"] == "call-ws"
    assert part["query"] == "llm 检索"
    assert part["results"][0]["url"] == "https://example.com/a"
    assert part["results"][0]["evidence_id"].startswith("ev_")
    assert "origin" not in part  # 主 Agent 自检索缺省无 origin（视为 main）
    tool_parts = [p for p in content["parts"] if p["type"] == "tool"]
    assert tool_parts[0]["output"] == "检索到 1 条来源"


def test_non_retrieval_tool_output_unchanged() -> None:
    """非检索工具：输出原样、无 retrieval part（无检索任务零变化）。"""
    content = _run_pipeline_events("sess-rsp", [
        {
            "event": "on_tool_start",
            "name": "some_other_tool",
            "run_id": "call-x",
            "data": {"input": {"x": 1}},
        },
        _tool_end_event("some_other_tool", "call-x", "原始输出"),
    ])
    assert [p["type"] for p in content["parts"]] == ["tool"]
    assert content["parts"][0]["output"] == "原始输出"


def test_retrieval_results_available_sse_payload_has_no_origin_by_default() -> None:
    bridge = LangGraphSseBridge("sess-sse")
    builder = AssistantMessageBuilder(session_id="sess-sse", message_id=bridge.assistant_message_id)
    ctx = new_stream_ctx()
    bridge.process_item(
        {
            "event": "on_tool_start",
            "name": "web_search",
            "run_id": "call-sse",
            "data": {"input": {"query": "q"}},
        },
        builder,
        ctx,
    )
    lines = bridge.process_item(
        _tool_end_event(
            "web_search", "call-sse",
            json.dumps({"results": [_web_search_result("https://example.com/a", "A")]}),
        ),
        builder,
        ctx,
    )
    raw = "".join(lines)
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in raw.split("\n")
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    retrieval = next(f for f in frames if f["type"] == "retrieval-results-available")
    # 向后兼容：主 Agent 自检索不携带 origin 字段（缺省 main）
    assert "origin" not in retrieval


# ---------------------------------------------------------------------------
# 子 Agent 执行器：任务级来源累积与通知携带（真实 create_agent 图）
# ---------------------------------------------------------------------------


class _ScriptedToolModel(BaseChatModel):
    script: list[AIMessage]
    _cursor: int = PrivateAttr(default=0)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        with self._lock:
            idx = self._cursor
            self._cursor += 1
        message = self.script[idx] if idx < len(self.script) else AIMessage(content="任务完成")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _search_tool(payloads: list[str]):
    state = {"calls": 0}

    @tool
    def web_search(query: str) -> str:
        """Search the web."""
        index = min(len(payloads) - 1, state["calls"])
        state["calls"] += 1
        return payloads[index]

    return web_search


def _wait_terminal(executor: BackgroundSubagentExecutor, task_id: str, timeout: float = 10.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        task = executor.get(task_id)
        assert task is not None
        if task["status"] in {
            BgTaskStatus.COMPLETED.value,
            BgTaskStatus.FAILED.value,
            BgTaskStatus.CANCELLED.value,
            BgTaskStatus.TIMED_OUT.value,
        }:
            return task
        time.sleep(0.05)
    raise AssertionError("task 未在期限内到达终态")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    bg_shutdown()
    notifications.drain("sess-rsp-exec")
    notifications.drain("sess-rsp-tools")
    drain_pending_sources("sess-rsp-exec")
    drain_pending_sources("sess-rsp-tools")
    drain_pending_sources("sess-rsp-main")


def test_subagent_accumulates_deduped_sources_and_notifies() -> None:
    """子 Agent 检索 → 任务级去重来源清单 + 终态通知携带结构化 sources。"""
    payloads = [
        json.dumps({
            "results": [
                _web_search_result("https://example.com/a?utm_source=x", "来源 A"),
                _web_search_result("https://example.com/b", "来源 B"),
            ],
        }, ensure_ascii=False),
        json.dumps({
            "results": [_web_search_result("https://example.com/a", "来源 A")],
        }, ensure_ascii=False),
    ]
    web_search = _search_tool(payloads)

    def worker_factory():
        return create_agent(
            _ScriptedToolModel(script=[
                AIMessage(content="", tool_calls=[
                    {"name": "web_search", "args": {"query": "q1"}, "id": "c1", "type": "tool_call"},
                ]),
                AIMessage(content="", tool_calls=[
                    {"name": "web_search", "args": {"query": "q2"}, "id": "c2", "type": "tool_call"},
                ]),
                AIMessage(content="调研完成"),
            ]),
            tools=[web_search],
            checkpointer=MemorySaver(),
            name="task-worker",
        )

    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=worker_factory, description="调研 X",
        session_id="sess-rsp-exec", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    assert task["status"] == BgTaskStatus.COMPLETED.value

    sources = BackgroundSubagentExecutor.sources_of(task_id)
    # 同一 canonical URL（带/不带 tracking 参数）合并为一条
    assert [s["url"] for s in sources] == [
        "https://example.com/a?utm_source=x",
        "https://example.com/b",
    ]

    notice = notifications.take_undelivered("sess-rsp-exec")[0]
    assert notice["label"] == "调研 X"
    assert isinstance(notice.get("sources"), list)
    assert len(notice["sources"]) == 2
    # 注入文本携带来源附录（有界）
    block = notifications.render_block([notice])
    assert "检索来源（去重后 2 条）" in block
    assert "https://example.com/a?utm_source=x" in block


def test_subagent_without_retrieval_has_no_sources_placeholder() -> None:
    def worker_factory():
        return create_agent(
            _ScriptedToolModel(script=[AIMessage(content="无检索任务完成")]),
            tools=[],
            checkpointer=MemorySaver(),
            name="task-worker",
        )

    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=worker_factory, description="纯计算",
        session_id="sess-rsp-exec", user_id="u1",
    )
    _wait_terminal(executor, task_id)
    assert BackgroundSubagentExecutor.sources_of(task_id) == []
    notice = notifications.take_undelivered("sess-rsp-exec")[0]
    assert "sources" not in notice
    assert "检索来源" not in notifications.render_block([notice])


# ---------------------------------------------------------------------------
# 3.x 跨边界登记：通知注入 / check_task / 桥接层收尾
# ---------------------------------------------------------------------------


def test_notify_middleware_registers_sources_and_keeps_injection_ephemeral() -> None:
    """通知注入：结构化 sources 进入跨边界登记；注入文本含附录且不落库。"""
    notifications.record(
        session_id="sess-rsp-main",
        task_id="child-1",
        status="completed",
        preview="调研完成",
        label="调研 X",
        sources=[_web_search_result("https://example.com/a", "来源 A")],
    )
    middleware = BgNotifyMiddleware(session_id="sess-rsp-main")
    messages = middleware._injected_messages()
    assert len(messages) == 1
    content = str(messages[0].content)
    assert content.startswith("[系统通知]")
    assert "检索来源（去重后 1 条）" in content
    # 登记发生在注入侧（收取 = 模型收到通知的这轮）
    pending = drain_pending_sources("sess-rsp-main")
    assert len(pending) == 1
    assert pending[0]["label"] == "调研 X"
    assert pending[0]["sources"][0]["url"] == "https://example.com/a"
    # 一次性：再注入不重复
    assert middleware._injected_messages() == []


def test_check_task_appends_sources_and_registers_pending() -> None:
    payloads = [
        json.dumps({"results": [_web_search_result("https://example.com/a", "来源 A")]}),
    ]
    web_search = _search_tool(payloads)

    def worker_factory():
        return create_agent(
            _ScriptedToolModel(script=[
                AIMessage(content="", tool_calls=[
                    {"name": "web_search", "args": {"query": "q"}, "id": "c1", "type": "tool_call"},
                ]),
                AIMessage(content="调研完成"),
            ]),
            tools=[web_search],
            checkpointer=MemorySaver(),
            name="task-worker",
        )

    executor = BackgroundSubagentExecutor(task_timeout_seconds=30)
    task_id = executor.start(
        worker_factory=worker_factory, description="调研 X",
        session_id="sess-rsp-tools", user_id="u1",
    )
    task = _wait_terminal(executor, task_id)
    child_id = task["child_session_id"] or task_id

    tools = build_background_task_tools(
        worker_factory=worker_factory,
        executor=executor,
        session_id="sess-rsp-tools",
        user_id="u1",
    )
    check = next(t for t in tools if t.name == "check_task")
    text = check.func(task_id)
    assert text.startswith(f"[{child_id}] completed")
    assert "检索来源（去重后 1 条）" in text
    assert "https://example.com/a" in text
    pending = drain_pending_sources("sess-rsp-tools")
    assert len(pending) == 1
    assert pending[0]["label"] == "调研 X"


def test_bridge_finish_registers_cross_boundary_parts_with_origin() -> None:
    """主 run 收尾：pending 来源落为带 origin 的 retrieval parts（该 assistant 消息上）。"""
    register_pending_sources(
        "sess-rsp-main", "调研 X",
        [_web_search_result("https://example.com/a?utm_source=x", "来源 A")],
    )
    bridge = LangGraphSseBridge("sess-rsp-main")
    builder = AssistantMessageBuilder(session_id="sess-rsp-main", message_id=bridge.assistant_message_id)
    ctx = new_stream_ctx()
    lines = bridge.process_item({"type": "__tw_finish__", "finish_reason": "stop"}, builder, ctx)

    parts = [p for p in builder.to_dict()["parts"] if p["type"] == "retrieval"]
    assert len(parts) == 1
    part = parts[0]
    assert part["origin"] == {"kind": "subagent", "label": "调研 X"}
    assert part["query"] == "调研 X"
    assert part["results"][0]["url"] == "https://example.com/a?utm_source=x"
    # 持久化权威 RunProjection 按帧重建：跨边界 parts 必须补发
    # retrieval-results-available 帧（先于 finish 帧），否则主消息落库缺失
    raw = "".join(lines)
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in raw.split("\n")
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    retrieval_frames = [f for f in frames if f["type"] == "retrieval-results-available"]
    assert len(retrieval_frames) == 1
    assert retrieval_frames[0]["origin"] == {"kind": "subagent", "label": "调研 X"}
    finish_idx = next(i for i, f in enumerate(frames) if f["type"] == "finish")
    retrieval_idx = frames.index(retrieval_frames[0])
    assert retrieval_idx < finish_idx
    # drain 一次性
    assert drain_pending_sources("sess-rsp-main") == []


def test_run_projection_persists_cross_boundary_origin() -> None:
    """RunProjection（主消息落库权威）消费跨边界帧：origin 透传进 parts。"""
    from noesis.chat.delivery.events import WireFrame
    from noesis.chat.runs.projection import RunProjection

    projection = RunProjection(
        run_id="run-rsp", user_id="u1", session_id="sess-rsp-main",
        assistant_message_id="msg-rsp", qa_type="SUPER_AGENT_QA",
    )
    projection.apply(WireFrame(
        event="retrieval-results-available",
        data={
            "type": "retrieval-results-available",
            "message_id": "msg-rsp",
            "tool_call_id": "subagent-sources-abc",
            "query": "调研 X",
            "results": [_web_search_result("https://example.com/a", "来源 A")],
            "origin": {"kind": "subagent", "label": "调研 X"},
        },
    ))
    parts = [p for p in projection.builder.to_dict()["parts"] if p["type"] == "retrieval"]
    assert len(parts) == 1
    assert parts[0]["origin"] == {"kind": "subagent", "label": "调研 X"}


def test_pending_sources_merge_for_same_label_and_url() -> None:
    """同任务多次 check / 通知+check 双通道：pending 内按 (label, 身份) 合并。"""
    source_a = _web_search_result("https://example.com/a", "来源 A")
    register_pending_sources("sess-rsp-main", "调研 X", [source_a])
    register_pending_sources("sess-rsp-main", "调研 X", [
        source_a,
        _web_search_result("https://example.com/b", "来源 B"),
    ])
    pending = drain_pending_sources("sess-rsp-main")
    assert len(pending) == 1
    assert [s["url"] for s in pending[0]["sources"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_multi_subagent_same_url_single_evidence_across_parts() -> None:
    """多子 Agent 同源（同 canonical URL）：主消息两个 origin part，evidence 去重为同一身份。"""
    register_pending_sources("sess-rsp-main", "调研 X", [
        _web_search_result("https://example.com/a?utm_source=x", "来源 A"),
    ])
    register_pending_sources("sess-rsp-main", "调研 Y", [
        _web_search_result("https://example.com/a", "来源 A"),
    ])
    bridge = LangGraphSseBridge("sess-rsp-main")
    builder = AssistantMessageBuilder(session_id="sess-rsp-main", message_id=bridge.assistant_message_id)
    ctx = new_stream_ctx()
    bridge.process_item({"type": "__tw_finish__", "finish_reason": "stop"}, builder, ctx)

    parts = [p for p in builder.to_dict()["parts"] if p["type"] == "retrieval"]
    assert len(parts) == 2
    assert {p["origin"]["label"] for p in parts} == {"调研 X", "调研 Y"}
    # 信封身份按原始 URL：两条 evidence 各自落库；「同 URL 合并为单条目带多
    # origin 徽标」发生在前端弧级聚合（canonical URL 去重），见前端回归测试。


def test_cross_boundary_registration_not_capped_by_per_call_limit() -> None:
    """跨边界登记不受单工具调用条数上限（30）截断：完整清单落库，面板计数真实。"""
    sources = [
        _web_search_result(f"https://example.com/doc/{i}", f"来源 {i}")
        for i in range(45)
    ]
    register_pending_sources("sess-rsp-main", "调研 X", sources)
    bridge = LangGraphSseBridge("sess-rsp-main")
    builder = AssistantMessageBuilder(session_id="sess-rsp-main", message_id=bridge.assistant_message_id)
    ctx = new_stream_ctx()
    bridge.process_item({"type": "__tw_finish__", "finish_reason": "stop"}, builder, ctx)
    parts = [p for p in builder.to_dict()["parts"] if p["type"] == "retrieval"]
    assert len(parts) == 1
    assert len(parts[0]["results"]) == 45


def test_child_pipeline_drain_is_noop_for_subagent_sessions() -> None:
    """子 Agent 管道复用同一桥接层：其 session 无 pending 登记，finish 不产生额外 parts。"""
    register_pending_sources("sess-rsp-main", "调研 X", [
        _web_search_result("https://example.com/a", "来源 A"),
    ])
    bridge = LangGraphSseBridge("sess-child")
    builder = AssistantMessageBuilder(session_id="sess-child", message_id=bridge.assistant_message_id)
    ctx = new_stream_ctx()
    bridge.process_item({"type": "__tw_finish__", "finish_reason": "stop"}, builder, ctx)
    assert builder.to_dict()["parts"] == []
    # 主会话的 pending 不被消费
    assert drain_pending_sources("sess-rsp-main") != []
