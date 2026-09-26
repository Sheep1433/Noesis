"""子代理执行内核：turn 链、投影落库、进度记录、worker 编译。

_arun 承载一轮或多轮 turn：追加消息队列非空则链式开下一轮，
协作停止在静止边界退出；_SubagentKind 是 kinds.py 行为协议的 subagent 实现。
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage
from noesis.chat.delivery.events import (
    RunAborted,
    RunCompleted,
    RunError,
    RunPaused,
    WireFrame,
)
from noesis.chat.event_mapping.langgraph_bridge import LangGraphSseBridge
from noesis.chat.event_mapping.mapper import RuntimeEventMapper, new_stream_ctx
from noesis.chat.event_mapping.usage_normalize import merge_usage
from noesis.chat.message_builder import AssistantMessageBuilder
from noesis.chat.runs import RunStatus
from noesis.agents.background.kinds import StopMode
from noesis.chat.event_mapping.retrieval import extract_deduped_sources, source_identity
from noesis.llm.finish_reason import normalize_provider_finish_reason
from noesis.llm.reasoning import set_request_reasoning_effort
from noesis.runtime.logging import logger
from noesis.runtime.stream import stream_agent_events

from noesis.agents.background.jobs.events import (
    _RUN_DELIVERY_LOCK,
    _delivery_core,
    _publish_run_event,
    _publish_task_event,
)
from noesis.agents.background.jobs.registry import (
    _TaskEntry,
    _config,
)
from noesis.agents.background.jobs.settle import (
    _STOP_TERMINALS,
    TaskTerminal,
    _try_transition,
    settle_delivery_failure,
    settle_stop,
    settle_task,
)
from noesis.agents.background.jobs.state import (
    BgTaskStatus,
    BackgroundTask,
    _PROGRESS_PREVIEW_CHARS,
)


def _progress_append(task: BackgroundTask, entry: dict[str, Any]) -> None:
    with task.progress_lock:
        task.progress.append(entry)

def _apply_context_snapshot(task: BackgroundTask, snapshot: dict[str, Any]) -> None:
    """统一管道 context-update 帧驱动：变更才发布 + 落库子会话 extra.context。

    与主对话同口径（usage.input_tokens 单轮真实值、每次覆盖）——
    快照提取已收敛到 bridge 的模型调用边界，executor 不再自行提取。
    """
    if not task.child_session_id:
        return
    if snapshot == getattr(task, "context_snapshot", None):
        return
    task.context_snapshot = dict(snapshot)
    _publish_run_event(task, "context-update", context=dict(snapshot))
    _schedule_context_persist(task, snapshot)

def _schedule_context_persist(task: BackgroundTask, snapshot: dict[str, Any]) -> None:
    """快照落库到子会话 extra.context（DB 引擎绑定主 loop，跨 loop 调度）。"""
    import datetime as _dt

    from noesis.runtime.main_loop import run_on_main_loop

    payload = {**snapshot, "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}

    async def _merge() -> None:
        try:
            from noesis.agents.background.ports import SessionOpsPort
            from noesis.storage.postgres.manager import pg_manager

            async with pg_manager.get_async_session_context() as db:
                await SessionOpsPort.merge_session_extra(
                    task.child_session_id, task.user_id, {"context": payload}, db=db,
                )
        except Exception:
            logger.opt(exception=True).warning(
                "bg subagent context snapshot persist failed task_id={}",
                task.task_id,
            )

    run_on_main_loop(_merge(), name=f"bg-ctx:{task.task_id}")

def _record_progress_from_model_end(task: BackgroundTask, message: AIMessage) -> str:
    """on_chat_model_end 边界的进度摘要：步数 +1 + 工具调用/文本预览。

    步数口径 = 模型调用次数，与子会话统计条 usage.steps（「N 轮 · M 步」、
    TTFT/步）同源；tool_calls / text / tool_result 只进预览不计步。
    返回本条消息的可见文本（task.result 口径）。
    """
    with task.progress_lock:
        task.step_count += 1
    text = _child_message_text(message)
    for call in getattr(message, "tool_calls", None) or []:
        _progress_append(
            task,
            {"kind": "tool_call", "name": str(call.get("name") or ""), "ts": time.time()},
        )
    if text.strip():
        _progress_append(
            task,
            {"kind": "text", "preview": text[:_PROGRESS_PREVIEW_CHARS], "ts": time.time()},
        )
    return text

def _record_progress_from_tool_end(task: BackgroundTask, message: Any) -> str:
    """on_tool_end 边界的进度摘要（工具结果预览，不计步）。"""
    text = _child_message_text(message)
    _progress_append(
        task,
        {
            "kind": "tool_result",
            "name": str(getattr(message, "name", None) or ""),
            "status": str(getattr(message, "status", None) or "success"),
            "preview": text[:_PROGRESS_PREVIEW_CHARS],
            "ts": time.time(),
        },
    )
    return text

def _child_message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")

def _fallback_terminal(fallback_error: Optional[str]) -> tuple[RunStatus, str]:
    """最后模型调用为降级失败说明时的 run 终态与 finish_reason。"""
    if fallback_error:
        return RunStatus.ERROR, "error"
    return RunStatus.COMPLETED, "stop"

def _final_model_fallback_error(message: Optional[AIMessage]) -> Optional[str]:
    """最后一次模型调用若为 LLM 降级失败说明，返回其文本。

    middleware 重试耗尽会返回 content 为失败文案的 AIMessage（带
    noesis_model_fallback 标记）；此时 run 不能标 completed——否则父 Agent
    会把「服务暂时不可用」当作子任务产出。
    """
    if message is None:
        return None
    from noesis.agents.middlewares.llm_error_handling_middleware import is_model_fallback_message

    if is_model_fallback_message(message):
        return _child_message_text(message) or "模型服务暂不可用"
    return None

async def _projection_boundary(task: BackgroundTask, builder: AssistantMessageBuilder) -> None:
    """投影边界统一处理：任务级来源合并（无条件）+ 子会话投影落库（有标准 run 时）。"""
    content = builder.to_dict()
    _merge_task_sources(task, content)
    if task.run_id:
        await _persist_child_projection(task, content)

def _merge_task_sources(task: BackgroundTask, content: dict[str, Any]) -> None:
    """投影内容中的 retrieval parts → 任务级去重来源清单（幂等合并）。"""
    for item in extract_deduped_sources(content):
        identity = source_identity(item)
        if identity and identity not in task.retrieval_sources:
            task.retrieval_sources[identity] = item

async def _persist_child_projection(
    task: BackgroundTask,
    content: dict[str, Any],
) -> None:
    """子会话投影落库（content 为统一管道 builder 产物）。

    投影序号 = 投递内核已提交的最后一帧序号（边界时 builder 内容与全部
    已提交帧一致）——DB guard 与投递事件同一个数空间，且不产生缓存空洞。
    内容恢复走 run-snapshot 快照 + 帧重放（message.updated 退役）。
    """
    if not task.run_id or not task.assistant_message_id:
        return
    core = _delivery_core(task.run_id)
    with _RUN_DELIVERY_LOCK:
        sequence = core.next_sequence - 1
    task.projection_sequence = sequence
    from noesis.agents.background.ports import SubagentSessionPort
    from noesis.runtime.main_loop import run_on_main_loop

    future = run_on_main_loop(
        SubagentSessionPort.persist_projection(
            run_id=task.run_id,
            assistant_message_id=task.assistant_message_id,
            content=copy.deepcopy(content),
            sequence=sequence,
        ),
        name=f"subagent-projection:{task.run_id}:{sequence}",
    )
    if future is not None:
        await asyncio.wrap_future(future)

async def _ensure_agent(entry: _TaskEntry) -> Any:
    """惰性编译 worker：factory 在隔离 loop 内调用，其 LLM 客户端 /
    checkpointer 连接池绑定隔离 loop（避免复用主 loop 实例的 cross-loop 风险）。

    entry.model_override 非 None 时以覆盖模型编译（追加消息切换模型后
    compiled_agent 已被置空，这里按新模型重建；同 thread 续跑，历史保留）。
    编译前设置本 turn 推理档位——档位在 LLM 构造时经 ContextVar 固化为
    请求参数，这里是所有编译路径（首轮/追加消息切参/审批 resume）的唯一处理入口。
    """
    if entry.compiled_agent is None:
        with entry.compiled_lock:
            if entry.compiled_agent is None:
                set_request_reasoning_effort(entry.turn_reasoning_effort)
                if entry.model_override is None:
                    result = entry.agent_factory()
                else:
                    result = entry.agent_factory(entry.model_override)
                if inspect.isawaitable(result):
                    result = await result
                entry.compiled_agent = result
    return entry.compiled_agent

def _apply_model_override(entry: _TaskEntry, model_id: Optional[str]) -> bool:
    """切换任务模型：更新 task.model_id（上下文窗口口径跟随）并使已编译
    worker 失效，下一 turn 以新模型编译。返回是否发生切换。"""
    if not model_id or model_id == entry.task.model_id:
        return False
    entry.task.model_id = model_id
    entry.model_override = model_id
    with entry.compiled_lock:
        entry.compiled_agent = None
    return True

@dataclass
class _TurnParams:
    """追加消息 turn 的执行参数：模型与推理档位均逐 turn 覆盖。"""

    model_id: Optional[str] = None
    reasoning_effort: Optional[str] = None

def _apply_turn_params(entry: _TaskEntry, params: Optional[_TurnParams]) -> bool:
    """应用 turn 参数：模型或推理档位任一变化即失效已编译 worker。

    档位在 LLM 构造时（factory 读 ContextVar）固化为请求参数，因此
    档位变化与模型变化一样需要重编译 worker（同 thread 续跑，历史保留）。
    字段缺省（None）= 沿用当前值，不视为覆盖（与 model_id 语义一致）：
    追加消息未指定档位时继承任务创建时捕获的档位。
    """
    if params is None:
        return False
    changed = _apply_model_override(entry, params.model_id)
    if (
        params.reasoning_effort is not None
        and params.reasoning_effort != entry.turn_reasoning_effort
    ):
        entry.turn_reasoning_effort = params.reasoning_effort
        with entry.compiled_lock:
            entry.compiled_agent = None
        changed = True
    return changed

def _pop_next_pending(entry: _TaskEntry) -> Optional[tuple[str, Optional[str], _TurnParams]]:
    with entry.pending_lock:
        if not entry.pending_messages:
            return None
        pending = entry.pending_messages.popleft()
        params = pending.params if isinstance(pending.params, _TurnParams) else None
        return pending.text, pending.message_id, params

@dataclass
class _TurnOutcome:
    """统一管道单 turn 的结果：驱动 executor 的终态 / 追加消息决策。"""

    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=dict)
    # 本 turn 每次模型调用明细（RunCompleted.model_calls 捕获），
    # 终态随 usage 一起落 message.extra.model_calls
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    # RunError 的用户可见消息（流异常经管道产出，等价旧值的异常路径）
    error_message: Optional[str] = None
    # 最后一次模型调用为 LLM 降级失败说明（终态不得标 completed）
    fallback_error: Optional[str] = None
    # 本 turn 最后一段可见文本（task.result 口径：倒序最后一条带文本消息）
    final_text: str = ""
    # 本 turn 的标准 multipart 投影（统一管道 builder 产物）
    content: dict[str, Any] = field(default_factory=lambda: {"version": 1, "parts": []})
    # 协作停止：停止请求已在静止边界受理，本 turn 提前退出走取消收尾
    cooperative_stop: bool = False
    # 输出截断（provider finish_reason=length）：本轮终态不得标 completed
    truncated: bool = False

class _TurnPipelineError(Exception):
    """统一管道报告的流错误（走 _arun 的既有异常收尾路径）。

    携带出错 turn 的 usage / model_calls：失败终态此前不落 usage，子会话
    统计条在失败后无数据可重建（只能靠实时统计兜底）——错误发生前模型
    调用的真实累计随终态一并落库。
    """

    def __init__(
        self,
        message: str,
        *,
        usage: Optional[dict[str, Any]] = None,
        model_calls: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.model_calls = model_calls

# 协作停止收尾标注：前缀只出现在 task.result / check_async_task 全文，不占通知预览预算

def _turn_run_status(outcome: "_TurnOutcome", fallback_error: Optional[str]) -> RunStatus:
    """turn 终态：降级失败 → ERROR；截断 → PARTIAL；否则 COMPLETED。"""
    status, _ = _fallback_terminal(fallback_error)
    if outcome.truncated and not fallback_error:
        status = RunStatus.PARTIAL
    return status

def _turn_finish_reason(outcome: "_TurnOutcome", fallback_error: Optional[str]) -> str:
    """turn finish_reason：error > truncated > 管道值（单处合成，两调用点共享）。"""
    if fallback_error:
        return "error"
    if outcome.truncated:
        return "truncated"
    return outcome.finish_reason or "stop"

def _turn_text_parts(outcome: "_TurnOutcome") -> str:
    """当前 turn 投影的全部 text parts（协作退出时的兜底提取）。"""
    return "\n".join(
        str(part.get("content") or "")
        for part in outcome.content.get("parts", [])
        if part.get("type") == "text" and part.get("content")
    ).strip()

async def _collect_persisted_text(task: BackgroundTask) -> str:
    """从子会话全部 assistant 消息投影提取 text parts（部分成果的权威来源）。

    覆盖全部轮次（追加消息链早轮）与硬杀场景（最后一次边界 persist 的投影）；
    无标准 run（测试/无 run_id）由调用方退回 turn 投影兜底。任何失败降级为空
    （spec：不阻塞终止）——包括端口缺方法的 AttributeError：该协程构造期
    同步抛出，必须整体包裹，否则会炸穿 settle_stop 使 run 永久 RUNNING。
    """
    if not (task.child_session_id and task.run_id):
        return ""
    try:
        from noesis.agents.background.ports import SubagentSessionPort
        from noesis.runtime.main_loop import run_on_main_loop

        future = run_on_main_loop(
            SubagentSessionPort.collect_partial_output(
                task.child_session_id, task.user_id,
            ),
            name=f"subagent-partial:{task.run_id}",
        )
        if future is None:
            return ""
        return (await asyncio.wrap_future(future)) or ""
    except Exception:
        # 提取失败降级为空；调用方兜底（turn 投影）
        logger.opt(exception=True).warning(
            "bg subagent partial output collect failed task_id={}", task.task_id,
        )
        return ""

def _merged_live_usage(entry: "_TaskEntry", bridge: Any) -> dict[str, Any]:
    """实时统计口径：已完成 turn 累计 + 当前 turn bridge 累计（数值相加）。

    bridge 每 turn 重建（message_usage 从零起算），跨轮合并保证与终态
    DB 重建（各 turn extra.usage 累加）一致；turns 按已完成轮次 +1。
    """
    merged = merge_usage(entry.accumulated_usage, bridge.message_usage)
    merged["turns"] = entry.task.turn_count + 1
    return merged

async def _run_turn_via_pipeline(
    entry: _TaskEntry,
    task: BackgroundTask,
    agent: Any,
    source: Any,
) -> _TurnOutcome:
    """单 turn 经统一管道执行：astream_events → RuntimeEventMapper → typed RunEvent。

    与主链路同一条事件映射（usage 累计 / 上下文快照语义同源）；
    本函数只做 executor 侧消费：进度摘要、子会话投影、快照发布与终态汇总。
    """
    session_id = task.child_session_id or task.task_id
    bridge = LangGraphSseBridge(
        session_id,
        assistant_message_id=task.assistant_message_id,
        model_id=task.model_id,
    )
    builder = AssistantMessageBuilder(
        session_id=session_id,
        message_id=task.assistant_message_id or bridge.assistant_message_id,
    )
    ctx = new_stream_ctx()
    mapper = RuntimeEventMapper(bridge)
    outcome = _TurnOutcome()
    last_ai_message: Optional[AIMessage] = None

    def _consume(events: list) -> None:
        for event in events:
            if isinstance(event, WireFrame):
                if event.event == "context-update":
                    snapshot = event.data.get("context")
                    if isinstance(snapshot, dict):
                        _apply_context_snapshot(task, snapshot)
                # 全部 bridge 帧经投递内核转发（与主链路同一帧词汇）：
                # delta / 实时统计为 transient（不占号不进缓存），边界帧
                # durable（占号可重放）；内容权威=落库检查点 + 快照恢复
                _publish_run_event(
                    task, event.event,
                    wire=dict(event.data),
                    transient=event.event in ("text-delta", "reasoning-delta", "stats-update"),
                )
                continue
            if isinstance(event, RunPaused):
                outcome.finish_reason = event.finish_reason or "paused"
                if event.usage:
                    outcome.usage = dict(event.usage)
                if event.model_calls:
                    outcome.model_calls = list(event.model_calls)
            elif isinstance(event, RunCompleted):
                outcome.finish_reason = event.finish_reason
                if event.usage:
                    outcome.usage = dict(event.usage)
                if event.model_calls:
                    outcome.model_calls = list(event.model_calls)
            elif isinstance(event, RunAborted):
                outcome.finish_reason = event.reason
            elif isinstance(event, RunError):
                outcome.error_message = event.message
                outcome.finish_reason = event.finish_reason or "error"

    stream_args = {
        "input": source,
        "config": _config(entry),
        "langfuse_session_id": session_id,
    }
    async for raw in stream_agent_events(
        agent,
        stream_args,
        task_id=task.task_id,
        message_id=task.assistant_message_id or "",
    ):
        _consume(mapper.map_item(raw, builder, ctx))
        raw_event = raw.get("event")
        # 进度摘要与投影边界：模型消息 / 工具结束（与 values 每步 diff 同可见节奏）
        if raw_event == "on_chat_model_end":
            output = (raw.get("data") or {}).get("output")
            if isinstance(output, AIMessage):
                last_ai_message = output
                text = _record_progress_from_model_end(task, output)
                if text.strip():
                    outcome.final_text = text
                # 实时统计（瞬态）：跨轮累计 + 本 turn bridge 累计，口径与
                # 终态 DB 重建一致；子会话详情页据此渲染主 Agent 同款统计行
                _publish_run_event(
                    task, "stats-update",
                    wire=_merged_live_usage(entry, bridge), transient=True,
                )
                # 输出截断一等终止：provider 以 length 截断（含参数被截的工具调用）
                if normalize_provider_finish_reason(
                    (getattr(output, "response_metadata", None) or {}).get("finish_reason")
                ) == "length":
                    outcome.truncated = True
                await _projection_boundary(task, builder)
                _publish_task_event(task, "progress")
                # 协作停止·静止边界：模型消息完整且无未应答工具调用
                if entry.cooperative_stop_signalled and not getattr(output, "tool_calls", None):
                    outcome.cooperative_stop = True
                    break
        elif raw_event == "on_tool_end":
            output = (raw.get("data") or {}).get("output")
            if output is not None:
                text = _record_progress_from_tool_end(task, output)
                if text.strip():
                    outcome.final_text = text
                await _projection_boundary(task, builder)
                _publish_task_event(task, "progress")
                # 协作停止·静止边界：工具结果已落定并投影
                if entry.cooperative_stop_signalled:
                    outcome.cooperative_stop = True
                    break
    if outcome.cooperative_stop:
        # 静止边界退出：投影已在边界发布（含最后一步产出）；usage 取已累计值
        outcome.fallback_error = _final_model_fallback_error(last_ai_message)
        outcome.content = builder.to_dict()
        return outcome
    # 流收尾（stream_agent_events 必产 __tw_finish__，此处为幂等兜底）
    _consume(mapper.finalize())
    outcome.fallback_error = _final_model_fallback_error(last_ai_message)
    outcome.content = builder.to_dict()
    # 最终投影：末段文本在 finish 时才 flush 进 builder，此处发布一次完整内容
    # （与旧 values 模式最后一个 chunk 含最终文本的可见节奏一致）
    if task.run_id and outcome.content.get("parts"):
        await _persist_child_projection(task, outcome.content)
    return outcome

async def _arun(
    entry: _TaskEntry,
    *,
    initial_source: Any = None,
) -> None:
    """执行一轮或多轮 turn。

    - start：initial_source 为原始 description 的 HumanMessage state
    - 冷恢复（deliver_message 对 completed 任务）：initial_source 为追加消息
    - kind="shell"：分派到 _arun_shell（无 worker / 无 turn 概念）
    turn 正常结束后若追加消息队列非空，链式开下一个 turn（同 thread
    追加 HumanMessage），队列清空前任务保持 running。
    """
    task = entry.task
    if task.status.is_terminal:
        # 调度窗口内已被 cancel（乐观终态已落）：本协程按未启动处理。
        # 停止族终态直接走停止终态处理（落库/事件/通知立即补齐——任务未
        # 执行、无产出可回收，终态处理本身很快）；其他终态意味着已有完整
        # 终态处理路径负责，不重复
        if task.status in _STOP_TERMINALS and not entry.terminal_published:
            await settle_stop(entry, task, None)
        return
    try:
        if task.run_id:
            from noesis.agents.background.ports import SubagentSessionPort
            from noesis.runtime.main_loop import run_on_main_loop

            started_future = run_on_main_loop(
                SubagentSessionPort.mark_started(task.run_id),
                name=f"subagent-start:{task.run_id}",
            )
            if started_future is not None:
                await asyncio.wrap_future(started_future)
        agent = await _ensure_agent(entry)
        # 首轮输入：initial_source（start 的 description / 冷恢复的追加消息）
        source = (
            initial_source
            if initial_source is not None
            else {"messages": [HumanMessage(content=task.prompt or task.description)]}
        )
        while True:
            outcome = await _run_turn_via_pipeline(entry, task, agent, source)
            if outcome.error_message is not None:
                # 管道产出的流错误：走既有异常收尾（task FAILED + run ERROR）
                raise _TurnPipelineError(
                    outcome.error_message,
                    usage=outcome.usage or None,
                    model_calls=outcome.model_calls or None,
                )
            if outcome.cooperative_stop:
                # 协作停止在静止边界退出：统一取消收尾（部分成果保留）
                await settle_stop(entry, task, outcome)
                return
            if outcome.final_text:
                task.result = outcome.final_text
            if outcome.truncated:
                # 截断一等终止（design D4）：task.result 走部分成果提取并标注截断原因，
                # 通知预览随之反映（「已完成」+ 截断标注而非伪装完整产出）
                partial = _turn_text_parts(outcome)
                task.result = (
                    f"输出截断（finish_reason=length）：\n{partial}"
                    if partial else "输出截断（finish_reason=length）"
                )
            # 实时统计的跨轮累计：本 turn usage 并入
            if outcome.usage:
                entry.accumulated_usage = merge_usage(
                    entry.accumulated_usage, outcome.usage,
                )
            # 追加消息链：队列非空则同 thread 开下一个 turn
            next_pending = _pop_next_pending(entry)
            if next_pending is None:
                break
            # 恢复 RUNNING 原子化并提前到 await 链之前：停止在 turn 收尾窗口受理时
            # 此处直接取消收尾（不再新开 run）；链内再受理由下一 turn 的静止边界退出
            if not _try_transition(task, BgTaskStatus.RUNNING):
                await settle_stop(entry, task, outcome)
                return
            next_message, next_user_message_id, next_params = next_pending
            # 该 turn 指定了新模型/新档位 → 失效已编译 worker，下一轮以新参数续跑同 thread
            # （档位 ContextVar 在 _ensure_agent 编译前统一设置）
            if _apply_turn_params(entry, next_params):
                agent = await _ensure_agent(entry)
            turn_fallback_error = outcome.fallback_error
            turn_status = _turn_run_status(outcome, turn_fallback_error)
            turn_reason = _turn_finish_reason(outcome, turn_fallback_error)
            if task.run_id:
                from noesis.agents.background.ports import SubagentSessionPort
                from noesis.runtime.main_loop import run_on_main_loop

                current_run_future = run_on_main_loop(
                    SubagentSessionPort.mark_terminal(
                        run_id=task.run_id,
                        status=turn_status,
                        content=copy.deepcopy(outcome.content),
                        error=turn_fallback_error,
                        finish_reason=turn_reason,
                        usage=outcome.usage or None,
                        model_calls=outcome.model_calls or None,
                    ),
                    name=f"subagent-turn-terminal:{task.run_id}",
                )
                if current_run_future is not None:
                    await asyncio.wrap_future(current_run_future)
                if entry.turn_factory is not None:
                    try:
                        launch = entry.turn_factory(
                            task.child_session_id or task.task_id,
                            next_message,
                            next_user_message_id,
                        )
                        if inspect.isawaitable(launch):
                            launch = await launch
                    except Exception as chain_exc:
                        # 链式投递失败不终态化任务：本 turn 已正常结束，消息行
                        # 翻转 dropped 后按队列耗尽收尾（剩余 pending 行保留，
                        # 供后续冷恢复重载）
                        from noesis.runtime.main_loop import run_on_main_loop as _run_flip

                        if next_user_message_id:
                            flip_future = _run_flip(
                                SubagentSessionPort.flip_pending_message_dropped(next_user_message_id),
                                name=f"bg-pending-drop:{next_user_message_id}",
                            )
                            if flip_future is not None:
                                await asyncio.wrap_future(flip_future)
                        logger.opt(exception=True).error(
                            "bg subagent chain delivery failed task_id={} message_id={}",
                            task.task_id, next_user_message_id,
                        )
                        break
                    task.run_id = str(launch.get("run_id") or "") or None
                    task.assistant_message_id = str(launch.get("assistant_message_id") or "") or None
                    task.turn_count += 1
                    task.projection_sequence = 0
                    task.completed_at = None
            logger.info(
                "bg subagent 追加消息 turn task_id={} queued={}",
                task.task_id,
                len(entry.pending_messages),
            )
            source = {"messages": [HumanMessage(content=next_message)]}
        final_fallback_error = outcome.fallback_error
        # 终态处理（先到获胜）：若停止已抢先受理（乐观 CANCELLED），
        # _accept_terminal 把本规格降级为停止语义并保留载荷
        await settle_task(
            entry,
            TaskTerminal(
                task_status=(
                    BgTaskStatus.FAILED if final_fallback_error else BgTaskStatus.COMPLETED
                ),
                run_status=_turn_run_status(outcome, final_fallback_error),
                finish_reason=_turn_finish_reason(outcome, final_fallback_error),
                error=final_fallback_error,
                content=copy.deepcopy(outcome.content),
                usage=outcome.usage or None,
                model_calls=outcome.model_calls or None,
            ),
        )
        logger.info(
            "bg subagent completed task_id={} steps={} duration={:.1f}s",
            task.task_id,
            task.step_count,
            task.completed_at - task.started_at,
        )
    except asyncio.CancelledError:
        # 硬杀兜底（停止宽限超时 / 沙箱销毁连坐）：完整终态收尾。
        # 投影沿用最后一次边界 persist（mark_terminal content=None 语义）；
        # 部分成果从落库投影回收（覆盖边界前产出；无 DB 降级为空）。
        # 乐观终态下 status 在受理时已落 CANCELLED/TIMED_OUT——守卫不得查
        # is_terminal（恒 False 导致本分支死亡），以 terminal_published（终态
        # 处理是否已发布）为准；停止信号已被冷恢复清除的旧协程消亡于此（不做
        # 终态处理，重新执行轮的生命周期归新协程）
        if not entry.terminal_published and (
            entry.cooperative_stop_signalled or not task.status.is_terminal
        ):
            await settle_stop(entry, task, None)
    except Exception as exc:
        # 终态处理已完整发布（状态+落库+事件）：迟到异常只记录，不覆盖终态、不重发
        if entry.terminal_published:
            logger.opt(exception=True).error(
                "bg subagent exception after terminal finalized task_id={}",
                task.task_id,
            )
            return
        # 停止受理中（乐观终态已落）：异常协程正在消亡，不会再有静止
        # 边界，立即走停止终态处理（保留部分成果回收），不再判 FAILED；
        # 普通异常走 FAILED（若终态已是停止族，_accept_terminal 自动
        # 降级保留载荷）
        if entry.cooperative_stop_signalled:
            await settle_stop(entry, task, None)
        else:
            await settle_task(
                entry,
                TaskTerminal(
                    task_status=BgTaskStatus.FAILED,
                    run_status=RunStatus.ERROR,
                    finish_reason="error",
                    error=str(exc),
                    usage=getattr(exc, "usage", None),
                    model_calls=getattr(exc, "model_calls", None),
                ),
            )
        logger.opt(exception=True).error(
            "bg subagent failed task_id={}",
            task.task_id,
        )

async def _arun_appended_turn(
    entry: _TaskEntry,
    text: str,
    user_message_id: Optional[str] = None,
    params: Optional[_TurnParams] = None,
) -> None:
    """completed child session 的新 turn：先建标准 run，再进入 worker。

    params 携带该 turn 的模型/推理档位覆盖；变化时以新参数编译 worker
    （同 thread 续跑）。新 turn 的投影由独立 builder 从零累积（统一管道）。

    前置段（worker 编译 / run 创建）失败经 settle_delivery_failure 处理：
    投递失败不终态化任务——回退先前终态（冷恢复窗口内受理的停止获胜）+
    消息行翻转 dropped + 异常记日志。deliver_message 对本协程 fire-and-forget，
    异常若不显式处理会滞留在未观察的 concurrent Future 里被静默吞掉（冷
    恢复静默失败事故：跨 loop 连接错误曾走此路径无任何日志）。
    """
    task = entry.task
    try:
        _apply_turn_params(entry, params)
        # 预编译 worker（参数变化时失效重编）；_arun 会复用缓存结果
        await _ensure_agent(entry)
        task.turn_count += 1
        if entry.turn_factory is not None:
            launch = entry.turn_factory(
                task.child_session_id or task.task_id,
                text,
                user_message_id,
            )
            if inspect.isawaitable(launch):
                launch = await launch
            task.run_id = str(launch.get("run_id") or "") or None
            task.assistant_message_id = str(launch.get("assistant_message_id") or "") or None
            task.projection_sequence = 0
    except Exception as exc:
        # 投递失败不终态化任务：回退先前终态 + 消息行 dropped + 可诊断错误
        # （冷恢复窗口内受理的停止终态获胜，settle_delivery_failure 内裁决）
        await settle_delivery_failure(entry, task, exc, user_message_id)
        return
    await _arun(entry, initial_source={"messages": [HumanMessage(content=text)]})

class _SubagentKind:
    """子 Agent 委派：可追问、有轮次概念、协作停止、协作超时。"""

    kind = "subagent"
    supports_message_append = True
    has_turns = True

    @staticmethod
    def reject_append_text() -> str:
        raise AssertionError("subagent 任务支持追加消息")  # pragma: no cover

    @staticmethod
    def run(entry: "_TaskEntry") -> Any:
        return _arun(entry)

    @staticmethod
    def request_stop(entry: "_TaskEntry") -> "StopMode":
        return StopMode.COOPERATIVE

    @staticmethod
    def on_timeout_locked(entry: "_TaskEntry") -> bool:
        return False  # 协作 timed_out：静止边界退出 + 宽限兜底
