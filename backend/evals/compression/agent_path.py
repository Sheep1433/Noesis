"""真 Agent 路径：fixture 落库并灌入真会话 → 真实压缩 → 每题独立 thread 作答。

三组对照与线上形态对齐（无自造提示词，作答侧即生产 SuperAgent）：
- ``uncompacted``：压缩关闭 + 完整原文历史 + 无会话检索（原生召回上限；
  与 current 只差压缩）
- ``current``：压缩后历史 + 无会话检索（旧线上形态；与 recovery 只差检索）
- ``recovery``：压缩后历史 + 生产 ``search_history``（新线上形态）

链路：
1. fixture 以生产持久化服务（``ChatService``）落库为 t_chat_session +
   t_chat_message，检索组调用的 ``search_history`` 搜的是真实 DB 数据
2. fixture 消息规范化（tool 消息转文本）后经 ``aupdate_state`` 灌入
   LangGraph checkpoint（与线上相同的 checkpointer 语义）
3. 线上 ``/compact`` 宿主路径触发压缩（``build_compaction_middleware`` +
   ``acompact_state``，经 checkpoint 适配图写状态）：显式命令语义，
   无合成消息、不依赖阈值，摘要失败返回 None 不留半态
4. 每题把「压缩后检查点」整体复制（``_fork_checkpoint``，压缩私有状态
   随行）到独立 thread：同题同摘要（对照只差工具），题间互不污染；
   经 ``stream_agent_events``（生产事件流层，含 Langfuse 回调）跑一轮
   真 Agent
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from evals.agent.runtime import AgentEventCollector, collect_agent_events
from evals.compression.fixture_loader import (
    _approx_token_counter,
    parse_fixture_messages,
)
from evals.compression.report import CLOSED_BOOK, RECOVERY, UNCOMPACTED

# 作答轮含工具循环的预算；超时记 error（completed=False），
# 与「信息不在、答不出」可区分。摘要大调用的时长由 REQUEST_TIMEOUT 控制
PROBE_TIME_BUDGET_SECONDS = 300

# 三组的配置差异（严格单变量对照链）：
# uncompacted 与 current 只差「压不压缩」（都不挂会话检索，测压缩净损失）；
# current 与 recovery 只差「挂不挂会话检索」（测检索净收益）。
# 不压缩组因此测的是「完整原文的原生召回上限」，不混入检索贡献
ARM_FLAGS: Dict[str, Dict[str, bool]] = {
    UNCOMPACTED: {"history_search_enabled": False, "compaction_enabled": False},
    CLOSED_BOOK: {"history_search_enabled": False, "compaction_enabled": True},
    RECOVERY: {"history_search_enabled": True, "compaction_enabled": True},
}


def _content_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "") if isinstance(block, dict) else str(block)
            for block in content
        ]
        return "\n".join(p for p in parts if p)
    return str(content or "")


def normalize_fixture_for_state(messages: List[AnyMessage]) -> List[AnyMessage]:
    """导出件没有 tool_call 配对结构：为每条 tool 消息回填配对的
    AIMessage tool_calls（id 确定性生成），产出生产状态形状
    （AIMessage(tool_calls) + ToolMessage）。

    工具结果是 ToolMessage 而非用户消息——压缩的用户原话装回、检索
    遮蔽语义都以角色为准；此前转写成 HumanMessage 会让装回预算被工具
    输出吃掉（实测 20K 预算 148 条里 131 条是工具转写、真用户只剩 17 条）。
    """
    normalized: List[AnyMessage] = []
    call_seq = 0
    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue
        if isinstance(msg, ToolMessage):
            name = msg.name or "tool"
            call_id = f"fixture_call_{call_seq}"
            call_seq += 1
            call = {"name": name, "args": {}, "id": call_id}
            for index in range(len(normalized) - 1, -1, -1):
                if isinstance(normalized[index], AIMessage):
                    paired = normalized[index]
                    normalized[index] = AIMessage(
                        content=paired.content,
                        tool_calls=[*paired.tool_calls, call],
                    )
                    break
            else:
                normalized.append(AIMessage(content="", tool_calls=[call]))
            normalized.append(ToolMessage(
                content=_content_str(msg.content), name=name, tool_call_id=call_id))
            continue
        normalized.append(msg)
    return normalized


def _group_db_rows(raw_messages: List[Dict[str, Any]]) -> List[tuple[str, list]]:
    """fixture 消息 → 生产行状分组：human → user 行；ai 与其后连续 tool
    消息合并为一条 assistant 行（text part + tool parts，对齐消息表 v2.1）。"""
    rows: List[tuple[str, list]] = []
    pending: Optional[list] = None

    def _flush() -> None:
        nonlocal pending
        if pending is not None:
            rows.append(("assistant", pending))
            pending = None

    for msg in raw_messages:
        mtype = str(msg.get("type") or "")
        content = str(msg.get("content") or "")
        if mtype == "human":
            _flush()
            rows.append(("user", [{"type": "text", "content": content}]))
        elif mtype in ("ai", "assistant"):
            _flush()
            pending = [{"type": "text", "content": content}] if content else []
        elif mtype == "tool":
            if pending is None:
                pending = []
            pending.append({
                "type": "tool",
                "name": str(msg.get("name") or "tool"),
                "input": {},
                "output": content,
            })
        elif mtype == "system":
            continue
        else:
            raise ValueError(f"未知 message type: {mtype}")
    _flush()
    return rows


async def seed_db_session(
    raw_messages: List[Dict[str, Any]], *, user_id: str, title: str
) -> str:
    """fixture 以生产持久化服务落库（search_history 的真实数据源）。"""
    from noesis.services.chat_service import ChatService
    from noesis.storage.postgres.manager import pg_manager

    pg_manager._ensure_engine()
    async with pg_manager.get_async_session_context() as db:
        session = await ChatService.create_session(
            user_id=user_id, title=title, kind="root", db=db)
        for role, parts in _group_db_rows(raw_messages):
            await ChatService.save_message(
                session_id=str(session.id),
                user_id=user_id,
                role=role,
                content={"version": 1, "parts": parts},
                db=db,
            )
    return str(session.id)


async def _compile_arm_agent(
    *, user_id: str, session_id: str, model_id: Optional[str], arm: str
):
    """按组差异编译真 SuperAgent（装配面与 run_agent 内部完全一致）。"""
    from noesis.agents.super_agent import SuperAgent

    host = SuperAgent()
    return await host._create_compiled_agent(
        user_id=user_id,
        session_id=session_id,
        model_id=model_id,
        mcp_tools=None,
        enabled_skills=None,
        file_list=None,
        db=None,
        disable_hitl=True,
        run_id=None,
        **ARM_FLAGS[arm],
    )


async def _fork_checkpoint(saver, *, src_thread: str, dst_thread: str) -> None:
    """把 src 线程的最新 checkpoint 原样复制到 dst 线程（LangGraph fork）。

    ``compaction`` 是 PrivateStateAttr：图内执行可写、``aupdate_state``
    公开接口会丢弃——压缩事件只能随 checkpoint 整体迁移，否则每题
    首次模型调用会重新压缩、current/recovery 摘要不共享。
    """
    tuple_ = await saver.aget_tuple({"configurable": {"thread_id": src_thread}})
    if tuple_ is None:
        raise RuntimeError(f"源线程无 checkpoint: {src_thread}")
    # MemorySaver 的消息内容存于按 thread 隔离的 blob 区，键 =
    # (thread, channel, version)：new_versions 必须带上源检查点的完整
    # 版本映射，否则目标线程只落检查点骨架、channel 值全空
    await saver.aput(
        {"configurable": {"thread_id": dst_thread, "checkpoint_ns": ""}},
        tuple_.checkpoint,
        tuple_.metadata,
        dict(tuple_.checkpoint.get("channel_versions") or {}),
    )


async def _run_turn(
    compiled,
    *,
    thread_id: str,
    query: str,
    langfuse_session_id: str,
    time_budget_seconds: int,
    seed_messages: Optional[List[AnyMessage]] = None,
    fork_from: Optional[str] = None,
) -> AgentEventCollector:
    """一轮真 Agent 对话（生产事件流层，含 Langfuse 回调）。

    两种接种方式二选一：``seed_messages`` 经 aupdate_state 播种原始消息
    （触发轮用）；``fork_from`` 复制源线程最新 checkpoint（含压缩私有
    状态，各题分 thread 用）。用量取事件流的 on_chat_model_end——摘要
    调用带 COMPACTION_SUMMARY_TAG 被生产事件流过滤，不计入（真实量见
    Langfuse trace）。
    """
    from noesis.runtime.stream import DEFAULT_RECURSION_LIMIT, stream_agent_events

    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": DEFAULT_RECURSION_LIMIT,
    }
    if seed_messages is not None:
        await compiled.aupdate_state(config, {"messages": list(seed_messages)})
    if fork_from is not None:
        await _fork_checkpoint(
            compiled.checkpointer, src_thread=fork_from, dst_thread=thread_id)

    collector = AgentEventCollector()

    async def _events():
        async for event in stream_agent_events(
            compiled,
            {
                "input": {"messages": [{"role": "user", "content": query}]},
                "config": config,
                "langfuse_session_id": langfuse_session_id,
                "qa_type": "SUPER_AGENT_QA",
            },
            task_id=thread_id,
            message_id=f"msg_{uuid.uuid4().hex[:16]}",
        ):
            yield event

    async def _cancel() -> None:
        return None

    await collect_agent_events(
        _events(), collector,
        timeout_seconds=time_budget_seconds, cancel=_cancel,
    )
    return collector


def _projected_context(post_values: Dict[str, Any]) -> tuple[List[AnyMessage], Optional[dict]]:
    """从触发轮后的状态取「压缩后有效上下文」（与中间件最终请求语义一致）。

    线上压缩是投影式：checkpoint 的 messages 始终保留完整原文，压缩事件
    （摘要消息 + 截断点）存私有状态 compaction。每次模型调用的最终请求 =
    [被压缩区用户原话（预算内倒序装回）, summary, *raw[cutoff:]]——用户
    原话装回复用生产 helper，预算取生产配置。返回 (有效上下文, 压缩事件)。
    """
    from noesis.agents.middlewares.compaction_middleware import _retained_user_messages
    from noesis.config.env import ModelConfig

    messages = list(post_values.get("messages") or [])
    policy = post_values.get("compaction")
    event = policy.get("event") if isinstance(policy, dict) else None
    if not isinstance(event, dict):
        return messages, None
    summary = event.get("summary_message")
    cutoff = event.get("cutoff_index")
    if not isinstance(summary, HumanMessage) or not isinstance(cutoff, int):
        return messages, None
    if cutoff < 0 or cutoff > len(messages):
        return messages, None
    users = _retained_user_messages(
        messages[:cutoff], ModelConfig.summarization_user_message_tokens
    )
    return [*users, summary, *messages[cutoff:]], event


def _compression_metrics(
    pre_messages: List[AnyMessage],
    post_values: Dict[str, Any],
) -> Dict[str, Any]:
    projected, event = _projected_context(post_values)
    pre_tokens = _approx_token_counter(pre_messages)
    post_tokens = _approx_token_counter(projected)
    summary_text = ""
    if event is not None:
        content = event.get("summary_message").content
        summary_text = content if isinstance(content, str) else str(content or "")
    return {
        "compressed": event is not None,
        "pre_tokens": pre_tokens,
        "post_tokens": post_tokens,
        "compression_ratio": (round(1.0 - post_tokens / pre_tokens, 4)
                              if pre_tokens > 0 else 0.0),
        "pre_message_count": len(pre_messages),
        "post_message_count": len(projected),
        "summary_text": summary_text,
        "summary_marker_found": event is not None,
    }


async def run_fixture_arms(
    fixture: Dict[str, Any],
    fixture_id: str,
    probes: List[Dict[str, Any]],
    arms: List[str],
    *,
    model_id: Optional[str],
    user_id: str,
    eval_run_id: str,
) -> Dict[str, Any]:
    """一次编排跑完三组：落库 → 编译 → /compact 触发 → 每题每组分 thread 作答。"""
    from noesis.config.user_data_paths import ensure_workspace_dir

    raw_messages = fixture["messages"]
    normalized = normalize_fixture_for_state(parse_fixture_messages(raw_messages))

    session_id = await seed_db_session(
        raw_messages,
        user_id=user_id,
        title=f"eval-compression-{fixture_id}-{eval_run_id}",
    )
    ensure_workspace_dir(user_id, session_id)
    langfuse_session = f"eval-compression-{fixture_id}-{eval_run_id}"

    agents = {
        arm: await _compile_arm_agent(
            user_id=user_id, session_id=session_id, model_id=model_id, arm=arm)
        for arm in arms
    }

    # 播种与 fork 源在压缩与否两条路径下都需要；三组共享同一
    # eval_runtime MemorySaver，用任一 agent 的 checkpointer 等价
    seed_agent = agents.get(RECOVERY) or agents.get(CLOSED_BOOK) or agents[arms[0]]
    saver = seed_agent.checkpointer
    seed_thread = f"{session_id}:seedbase"
    await seed_agent.aupdate_state(
        {"configurable": {"thread_id": seed_thread}},
        {"messages": list(normalized)},
    )
    # 不压缩组的 fork 源：播种后、压缩前的原始状态
    pre_compact_thread = f"{session_id}:seed"
    await _fork_checkpoint(
        saver, src_thread=seed_thread, dst_thread=pre_compact_thread)
    seed_config = {"configurable": {"thread_id": seed_thread}}

    # 触发 = 线上 /compact 宿主路径：build_compaction_middleware +
    # acompact_state，经 checkpoint 适配图写状态（与 compact_session 服务
    # 同构，仅模型绑定来自评测快照而非会话配置）。显式命令语义：无合成
    # 消息、不依赖阈值、失败返回 None 不留半态。
    # 只有不压缩组时跳过：参照组不经过压缩链路，触发一次 fixture 体量的
    # 大输入摘要纯浪费
    trigger_agent = agents.get(RECOVERY) or agents.get(CLOSED_BOOK)
    compression = None
    if trigger_agent is None:
        # 只有不压缩组：跳过压缩触发（参照组不经过压缩链路）
        print(f"  {fixture_id} 仅不压缩组在场，跳过压缩触发", flush=True)
    else:
        from noesis.factory import build_compaction_middleware
        middleware = build_compaction_middleware(model_id=model_id, session_id=session_id)
        if middleware is None:
            raise RuntimeError("压缩评测需要 summarization 可用（中间件构造为空）")
        from langchain.agents import create_agent as _create_adapter
        from noesis.llm import get_llm
        # 适配图仅作 checkpoint 写入载体，模型节点不会被调用（无模型轮次）
        adapter = _create_adapter(
            model=get_llm(model_id=model_id), tools=[], system_prompt="",
            middleware=[middleware], checkpointer=trigger_agent.checkpointer,
        )
        print(f"  {fixture_id} /compact 宿主路径触发压缩（pre_tokens≈"
              f"{_approx_token_counter(normalized):,}）...", flush=True)

        # 压缩重试：摘要网关对超大输入偶发返回空响应（实测 9 秒空内容 vs
        # 正常 84 秒真摘要）——失败不留半态，同线程直接重试；current/recovery
        # 在场时全部失败即硬错（未压缩继续跑会产出貌似合理实为无效的对照数据）
        from noesis.agents.middlewares.compaction_middleware import _summary_is_invalid
        max_compact_attempts = 3
        for attempt in range(1, max_compact_attempts + 1):
            snapshot = await adapter.aget_state(seed_config)

            async def _checkpoint(update: dict) -> None:
                await adapter.aupdate_state(seed_config, update, as_node="model")

            # thread_id 传 session_id：acompact_state 用它写 t_chat_session 压缩
            # 边界（before_compaction 检索语义依赖），状态写入走 checkpoint 闭包
            compacted = await middleware.acompact_state(
                snapshot.values, session_id, checkpoint=_checkpoint)
            candidate = None
            if compacted is not None:
                post_values = dict((await adapter.aget_state(seed_config)).values or {})
                candidate = _compression_metrics(normalized, post_values)
                # 双保险：acompact_state 内部已过 _summary_is_invalid，此处再核
                # 结构指标（曾实测退化摘要骗过结构判定）
                if not candidate["compressed"] or _summary_is_invalid(candidate["summary_text"]):
                    candidate = None
            if candidate is not None:
                compression = candidate
                break
            print(
                f"  压缩未成功（尝试 {attempt}/{max_compact_attempts}，"
                "常见原因：摘要网关空响应），重试...", file=sys.stderr, flush=True)
        if compression is None:
            raise RuntimeError(
                f"压缩失败（pre_tokens≈{_approx_token_counter(normalized):,}，"
                f"{max_compact_attempts} 次尝试均失败，常见原因：摘要网关对超大"
                "输入返回空响应）。稍后重跑，或换更小的 fixture")

    try:
        arm_outputs: Dict[str, Any] = {}
        for arm in arms:
            # 不压缩组 fork 播种检查点（完整原文、无压缩事件）；
            # 其余组 fork 触发后检查点（同题同摘要，对照唯一差异 = 工具）
            fork_source = pre_compact_thread if arm == UNCOMPACTED else seed_thread
            probe_runs = []
            for probe in probes:
                print(f"  {fixture_id} [{arm}] probe {probe['id']} ...", flush=True)
                collector = await _run_turn(
                    agents[arm],
                    thread_id=f"{session_id}:{arm}:{probe['id']}",
                    fork_from=fork_source,
                    query=str(probe["question"]),
                    langfuse_session_id=langfuse_session,
                    time_budget_seconds=PROBE_TIME_BUDGET_SECONDS,
                )
                if not collector.completed:
                    # 回合故障（超时/网关降级）重试一次：completed=False 不是
                    # 「答错」——模型答错也会正常完轮。重试不改变测量语义，
                    # 只滤掉环境噪声（并发评测共享网关时限流实测会发生）。
                    # 退避 20s：实测压缩后的首个 766K 大调用连续两次被网关
                    # 降级，紧贴重试同样撞限流
                    print(
                        f"  {fixture_id} [{arm}] probe {probe['id']} 未完成"
                        f"（{collector.error}），退避 20s 后重试一次", flush=True)
                    await asyncio.sleep(20)
                    collector = await _run_turn(
                        agents[arm],
                        thread_id=f"{session_id}:{arm}:{probe['id']}",
                        fork_from=fork_source,
                        query=str(probe["question"]),
                        langfuse_session_id=langfuse_session,
                        time_budget_seconds=PROBE_TIME_BUDGET_SECONDS,
                    )
                probe_runs.append({
                    "continuation_text": collector.final_text,
                    "completed": collector.completed,
                    "error": collector.error,
                    "tool_stats": dict(collector.tool_stats),
                    "input_tokens": collector.input_tokens,
                    "output_tokens": collector.output_tokens,
                })
            arm_outputs[arm] = {
                "session_id": session_id,
                "probes": probe_runs,
                "usage": {
                    "input_tokens": sum(p["input_tokens"] for p in probe_runs),
                    "output_tokens": sum(p["output_tokens"] for p in probe_runs),
                },
            }

        return {
            "session_id": session_id,
            "compression": compression,
            # 摘要调用发生在 acompact_state 内（无事件流），usage 不可采；
            # 真实量以 609K fixture 实测约 75 万 in 为参考口径
            "trigger_usage": {"input_tokens": 0, "output_tokens": 0},
            "arms": arm_outputs,
        }
    finally:
        # 评测会话沙箱用毕即毁：历次 run 各建一个容器，攒满 runner 上限
        # （sandbox_max_replicas）会阻塞后续运行；作答中途崩溃同样要回收
        try:
            from noesis.agents.backends.sandbox_lifecycle import destroy_session_sandbox

            await destroy_session_sandbox(user_id, session_id)
        except Exception:  # noqa: BLE001
            pass  # 沙箱回收尽力而为
        # 引擎处置：本函数经每 fixture 一次的 asyncio.run 进入，pg_manager
        # 连接池绑定本轮 loop；不关的话下一 fixture 的新 loop 会取到绑定
        # 已关闭 loop 的连接（asyncpg 跨 loop 崩溃，多 fixture 实证）。
        # close() 后引擎懒重建，下一 fixture 在自己的 loop 上重新建池。
        try:
            from noesis.storage.postgres.manager import pg_manager

            await pg_manager.close()
        except Exception:  # noqa: BLE001
            pass
